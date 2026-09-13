#!/usr/bin/env python3
"""Real compositor + real GTK + real MCP stdio, with GUI callback assertions.

No mocked backend. A skipped/failed environment is never reported as a pass.
"""
import argparse
import base64
import hashlib
import json
import os
import pathlib
import queue
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/linux-computer-use/scripts"))


class Client:
    def __init__(self, output, command=None):
        self.stderr = open(output / "server.log", "w")
        entry = os.environ.get("CUL_TEST_ENTRY", str(ROOT / "skills/linux-computer-use/scripts/cul.py"))
        base = pathlib.Path(entry).parent
        digest = hashlib.sha256()
        for path in sorted([pathlib.Path(entry)] + list((base / "cul").glob("*.py"))):
            digest.update(str(path.relative_to(base)).encode() + b"\0" + path.read_bytes())
        self.runtime_sha256 = digest.hexdigest()
        self.process = subprocess.Popen(command or [sys.executable, entry, "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, text=True, bufsize=1,
            env=dict(os.environ, CUL_DEBUG_TRACE="1"))
        self.messages = queue.Queue()
        self.index = 0
        self.timings = {}
        def read():
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.messages.put({"error": "Non-JSON stdout: " + line})
        threading.Thread(target=read, daemon=True).start()

    def send(self, message):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def request(self, method, params=None):
        self.index += 1
        self.send({"jsonrpc": "2.0", "id": self.index, "method": method, "params": params or {}})
        answer = self.messages.get(timeout=150 if params and params.get("name") == "start_session" else 20)
        assert answer.get("id") == self.index, answer
        assert "error" not in answer, answer
        return answer["result"]

    def tool(self, name, args=None, expect_error=False):
        started = time.perf_counter()
        answer = self.request("tools/call", {"name": name, "arguments": args or {}})
        self.timings.setdefault(name, []).append((time.perf_counter() - started) * 1000)
        if expect_error:
            assert answer.get("isError"), answer
            return answer
        assert not answer.get("isError"), answer
        value = answer["structuredContent"]
        images = [b for b in answer["content"] if b["type"] == "image"]
        if images:
            value["image"]["data"] = images[0]["data"]
        return value

    def close(self):
        if self.process.poll() is None:
            try:
                self.tool("stop")
            except Exception:
                self.process.send_signal(signal.SIGUSR1)
                time.sleep(.1)
            self.process.stdin.close()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.stderr.close()


def wait_state(path, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        if path.exists():
            value = json.loads(path.read_text())
            if predicate(value):
                return value
        time.sleep(.03)
    raise AssertionError(f"GUI callback condition not met: {value}")


def colour(image, rgb):
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    loader = GdkPixbuf.PixbufLoader()
    loader.write(base64.b64decode(image["data"]))
    loader.close()
    pixbuf = loader.get_pixbuf()
    pixels, channels, stride = pixbuf.get_pixels(), pixbuf.get_n_channels(), pixbuf.get_rowstride()
    points = []
    for y in range(0, pixbuf.get_height(), 2):
        for x in range(0, pixbuf.get_width(), 2):
            pos = y * stride + x * channels
            if all(abs(pixels[pos+i] - rgb[i]) <= 10 for i in range(3)):
                points.append((x, y))
    assert len(points) > 30, f"Expected visible colour {rgb}, got {len(points)} pixels"
    return ((min(p[0] for p in points) + max(p[0] for p in points)) / 2,
            (min(p[1] for p in points) + max(p[1] for p in points)) / 2)


def run(args):
    output = pathlib.Path(args.output).absolute()
    output.mkdir(parents=True, exist_ok=True)
    fixture = output / "fixture.json"
    fixture.unlink(missing_ok=True)
    private = None
    consent_done = threading.Event()
    consent_evidence = []
    if args.portal_private_consent:
        # Only an explicitly opted-in test may accept a dialog, and only on
        # a compositor/bus created here. Never automate consent on the host.
        from cul.engine import Engine
        private = Engine()
        private.start(compositor="kwin")
        root = private.desktop.root
        assert os.environ["XDG_RUNTIME_DIR"] == str(root)
        display_path = root / os.environ["WAYLAND_DISPLAY"]
        assert display_path.is_socket() and display_path.parent == root
        assert os.environ["DBUS_SESSION_BUS_ADDRESS"] != private.desktop.original_env.get("DBUS_SESSION_BUS_ADDRESS")
        bus_pid = int(private.windowing.bus.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", ("org.freedesktop.DBus",)))
        assert bus_pid == private.desktop.processes[0].pid
        (output / "isolation-proof.json").write_text(json.dumps({"private_wayland_socket": str(display_path),
            "private_bus_pid": bus_pid, "owned_bus_pid": private.desktop.processes[0].pid,
            "session_bus": os.environ["DBUS_SESSION_BUS_ADDRESS"], "host_bus_is_different": True}, indent=2))
        def consent():
            while not consent_done.wait(.25):
                try:
                    windows = private.list_windows()["windows"]
                    dialog = next((w for w in windows if w["title"] == "Remote control requested"), None)
                    if not dialog:
                        continue
                    state = private.get_state(pid=dialog["pid"], screenshot=False)
                    nodes = state["accessibility"]["nodes"]
                    identity = next((n["name"] for n in nodes if "Linux Computer Use requested access" in n["name"]), None)
                    if not identity:
                        continue
                    restore = next((n for n in nodes if n["role"] == "check box" and "checked" in n["states"]), None)
                    if restore:
                        private.perform_action(restore["token"])
                        continue
                    share = next((n for n in nodes if n["role"] == "button" and n["name"] == "Share"), None)
                    if share:
                        picture = private.screenshot(format="png")["image"]
                        (output / "private-consent.png").write_bytes(base64.b64decode(picture["data"]))
                        consent_evidence.append({"identity": identity, "private_directory": str(private.desktop.root)})
                        private.perform_action(share["token"])
                        return
                except Exception as error:
                    consent_evidence.append({"error": str(error)})
        threading.Thread(target=consent, daemon=True).start()
    client = Client(output)
    checks = []
    report = {"compositor": args.compositor, "checks": checks, "status": "failed", "runtime_sha256": client.runtime_sha256}
    pids = []
    try:
        init = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "cul-e2e", "version": "1"}})
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = client.request("tools/list")["tools"]
        assert len(tools) == 19
        checks.append("MCP initialization, tool discovery, typed image blocks")
        session = client.tool("start_session", {"mode": "current" if private else args.mode,
            "backend": "portal" if private else "auto", "compositor": args.compositor})
        consent_done.set()
        report["session"] = session
        if private:
            report["private_portal_consent"] = consent_evidence
            assert any("identity" in e for e in consent_evidence), consent_evidence
            checks.append("RemoteDesktop portal consent under the correct identity on an owned private desktop")
            pids = [p.pid for p in private.desktop.processes]
        if session.get("private_directory"):
            pids = json.loads((pathlib.Path(session["private_directory"]) / "session.json").read_text())["pids"]
        app = client.tool("launch_app", {"argv": [sys.executable, str(ROOT / "tests/fixture.py"), str(fixture)]})
        pids.append(app["pid"])
        wait_state(fixture, lambda s: s["ready"])
        # Allow the first frame and native accessibility registration to arrive.
        time.sleep(.35)
        windows = client.tool("list_windows")["windows"]
        window = next(w for w in windows if w["pid"] == app["pid"])
        client.tool("focus_window", {"window_id": window["id"]})
        deadline = time.monotonic() + 5
        while True:
            state = client.tool("get_state", {"pid": app["pid"]})
            nodes = state["accessibility"]["nodes"]
            entry = next((n for n in nodes if n["name"] == "Unicode test entry"), None)
            if entry is not None or time.monotonic() > deadline:
                break
            time.sleep(.1)
        assert entry is not None, state
        assert "editable" in entry["states"]
        assert not state["accessibility"]["errors"], state["accessibility"]["errors"]
        checks.append("Native window discovery/focus and AT-SPI editable field")
        initial = client.tool("screenshot", {"format": "png"})["image"]
        (output / "before.png").write_bytes(base64.b64decode(initial["data"]))
        colour(initial, (51, 204, 166))
        phrase = "Bonjour SteamOS — café français 日本語 🐧"
        client.tool("type_text", {"text": phrase})
        wait_state(fixture, lambda s: s["text"] == phrase)
        client.tool("press_key", {"key": "Ctrl+a"})
        client.tool("type_text", {"text": phrase + " ✓"})
        wait_state(fixture, lambda s: s["text"] == phrase + " ✓")
        checks.append("Real UTF-8 keyboard paste and Ctrl+A replacement")
        small = client.tool("screenshot", {"format": "png", "max_width": 640})["image"]
        assert small["width"] == 640 and small["scale_x"] == 2
        bx, by = colour(small, (49, 95, 168))
        client.tool("click", {"x": bx, "y": by, "frame_id": small["frame_id"]})
        wait_state(fixture, lambda s: s["clicks"] == 1)
        client.tool("click", {"x": bx, "y": by, "frame_id": small["frame_id"], "count": 2})
        wait_state(fixture, lambda s: s["clicks"] == 3)
        checks.append("Pixel click/double-click through 2× screenshot coordinate mapping")
        gx, gy = colour(small, (51, 204, 166))
        yx, yy = colour(small, (230, 166, 51))
        client.tool("drag", {"start_x": gx, "start_y": gy, "end_x": yx, "end_y": yy,
                             "frame_id": small["frame_id"], "duration_ms": 250})
        drag = wait_state(fixture, lambda s: s["drag"] is not None)["drag"]
        assert drag["end"][0] - drag["start"][0] > 250, drag
        checks.append("Pointer drag with native press/motion/release callbacks")
        client.tool("scroll", {"x": gx, "y": gy + 100, "dy": 90, "frame_id": small["frame_id"]})
        wait_state(fixture, lambda s: s["scroll"] > 0)
        checks.append("Real scroll adjustment changed")
        state = client.tool("get_state", {"pid": app["pid"], "screenshot": False})
        nodes = state["accessibility"]["nodes"]
        button = next(n for n in nodes if n["name"] == "Verify click")
        client.tool("perform_action", {"token": button["token"]})
        wait_state(fixture, lambda s: s["clicks"] == 4)
        client.tool("perform_action", {"token": button["token"]}, expect_error=True)
        state = client.tool("get_state", {"pid": app["pid"], "screenshot": False})
        entry = next(n for n in state["accessibility"]["nodes"] if n["name"] == "Unicode test entry")
        client.tool("set_value", {"token": entry["token"], "value": "AT-SPI direct value ✓"})
        wait_state(fixture, lambda s: s["text"] == "AT-SPI direct value ✓")
        checks.append("Focus-free semantic button/text actions; stale element rejected")
        client.tool("click", {"x": 9000, "y": 9000}, expect_error=True)
        client.tool("click", {"x": 1, "y": 1, "frame_id": "invalid"}, expect_error=True)
        client.tool("drag", {"start_x": 1, "start_y": 1, "end_x": 2, "end_y": 2, "duration_ms": -1}, expect_error=True)
        checks.append("Invalid bounds, stale frame and invalid duration rejected")
        state = client.tool("get_state", {"pid": app["pid"], "screenshot": False})
        entry = next(n for n in state["accessibility"]["nodes"] if n["name"] == "Unicode test entry")
        batch = client.tool("batch", {"actions": [
            {"tool": "focus_element", "arguments": {"token": entry["token"]}},
            {"tool": "press_key", "arguments": {"key": "Ctrl+a"}},
            {"tool": "type_text", "arguments": {"text": "E2E complete — Linux 🐧"}}]})
        assert batch["completed"]
        wait_state(fixture, lambda s: s["text"] == "E2E complete — Linux 🐧")
        after = client.tool("screenshot", {"format": "png"})["image"]
        assert after["sha256"] != initial["sha256"]
        (output / "after.png").write_bytes(base64.b64decode(after["data"]))
        checks.append("Serialized batch and visibly changed post-action screenshot")
        measurements = []
        for _ in range(args.samples):
            start = time.perf_counter()
            client.tool("screenshot")
            measurements.append((time.perf_counter() - start) * 1000)
        report["screenshot_roundtrip_ms"] = {"samples": len(measurements), "median": round(statistics.median(measurements), 2),
            "p95": round(sorted(measurements)[max(0, int(len(measurements) * .95) - 1)], 2), "min": round(min(measurements), 2)}
        repaint = []
        for index in range(5):
            state = client.tool("get_state", {"pid": app["pid"], "screenshot": False})
            button = next(n for n in state["accessibility"]["nodes"] if n["name"] == "Verify click")
            started = time.perf_counter()
            client.tool("perform_action", {"token": button["token"]})
            wait_state(fixture, lambda s: s["clicks"] == 5 + index)
            shot = client.tool("screenshot", {"format": "png"})["image"]
            assert shot["sha256"] != after["sha256"]
            assert shot["after_action_frame"], shot
            after = shot
            repaint.append((time.perf_counter() - started) * 1000)
        report["action_to_verified_screenshot_ms"] = {"samples": len(repaint),
            "median": round(statistics.median(repaint), 2), "max": round(max(repaint), 2)}
        checks.append("Five acknowledged GUI actions each produced a fresh, changed screenshot")
        report["final_gui_state"] = json.loads(fixture.read_text())
        # Exercise MCP cancellation while a pointer button is down.
        fresh = client.tool("screenshot", {"format": "png", "max_width": 640})["image"]
        gx, gy = colour(fresh, (51, 204, 166))
        yx, yy = colour(fresh, (230, 166, 51))
        client.index += 1
        request_id = client.index
        client.send({"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {"name": "drag",
            "arguments": {"start_x": gx, "start_y": gy, "end_x": yx, "end_y": yy,
                          "frame_id": fresh["frame_id"], "duration_ms": 5000}}})
        wait_state(fixture, lambda s: s["pointer_down"])
        client.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": request_id}})
        cancelled = client.messages.get(timeout=5)
        assert cancelled["result"].get("isError"), cancelled
        wait_state(fixture, lambda s: not s["pointer_down"] and s["drag"] and s["drag"]["end"][0] - s["drag"]["start"][0] < 100)
        checks.append("MCP cancellation releases a held drag button")
        client.close()
        report["server_returncode"] = client.process.returncode
        assert client.process.returncode == 0, f"MCP server exited abnormally: {client.process.returncode}"
        if private:
            private.close()
            time.sleep(.3)
        if args.mode == "isolated":
            alive = [pid for pid in pids if pathlib.Path(f"/proc/{pid}").exists()]
            report["remaining_processes"] = [{"pid": pid,
                "stat": pathlib.Path(f"/proc/{pid}/stat").read_text(),
                "command": pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")}
                for pid in alive]
            assert not alive, f"Owned processes survived cleanup: {alive}"
            checks.append("Owned compositor, buses, services and application cleaned up")
        report["status"] = "passed"
    except BaseException as error:
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
        print(report["traceback"], file=sys.stderr)
    finally:
        directory = report.get("session", {}).get("private_directory")
        if directory and pathlib.Path(directory).exists():
            logs = output / "desktop-logs"
            logs.mkdir(exist_ok=True)
            for path in pathlib.Path(directory).glob("*.log"):
                shutil.copyfile(path, logs / path.name)
        client.close()
        consent_done.set()
        if private:
            private.close()
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--compositor", default="kwin")
    parser.add_argument("--mode", default="isolated", choices=["isolated", "current"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--portal-private-consent", action="store_true",
        help="Explicitly authorize the test to accept portal consent in its own private KWin only")
    sys.exit(run(parser.parse_args()))
