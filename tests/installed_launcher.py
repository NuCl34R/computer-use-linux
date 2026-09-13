#!/usr/bin/env python3
"""Drive a real application through the actual installed native/container launcher."""
import argparse
import base64
import json
import os
import pathlib
import time
from e2e import Client, ROOT, wait_state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launcher", type=pathlib.Path, required=True)
    parser.add_argument("--runtime", choices=["native", "container"], required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=True)
    state_file = output / "fixture.json"
    state_file.unlink(missing_ok=True)
    if args.runtime == "container":
        # Explicit file mapping, not a host display or session-bus mount.
        output.relative_to(ROOT)
        os.environ.update(CUL_WORKDIR=str(ROOT), CUL_NETWORK="none")
        fixture = pathlib.Path("/work/tests/fixture.py")
        remote_state = pathlib.Path("/work") / state_file.relative_to(ROOT)
    else:
        fixture = ROOT / "tests/fixture.py"
        remote_state = state_file
    client = Client(output, command=[str(args.launcher.absolute()), "mcp"])
    report = {"runtime": args.runtime, "status": "failed", "checks": []}
    try:
        client.request("initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "installed-launcher-e2e", "version": "1"}, "capabilities": {}})
        assert len(client.request("tools/list")["tools"]) == 19
        session = client.tool("start_session", {"mode": "isolated"})
        report["backend"] = session["backend"]
        app = client.tool("launch_app", {"argv": ["python3", str(fixture), str(remote_state)]})
        wait_state(state_file, lambda s: s["ready"])
        deadline = time.monotonic() + 8
        while True:
            state = client.tool("get_state", {"pid": app["pid"]})
            entry = next((n for n in state["accessibility"]["nodes"] if n["name"] == "Unicode test entry"), None)
            if entry:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("GTK entry did not appear in AT-SPI")
        before = client.tool("screenshot", {"format": "png"})["image"]
        state = client.tool("get_state", {"pid": app["pid"], "screenshot": False})
        entry = next(n for n in state["accessibility"]["nodes"] if n["name"] == "Unicode test entry")
        client.tool("focus_element", {"token": entry["token"]})
        phrase = "Installed launcher — café 日本語 🐧"
        client.tool("type_text", {"text": phrase})
        wait_state(state_file, lambda s: s["text"] == phrase)
        after = client.tool("screenshot", {"format": "png"})["image"]
        assert after["sha256"] != before["sha256"] and after["after_action_frame"]
        (output / "after.png").write_bytes(base64.b64decode(after["data"]))
        report["checks"] = ["Actual installed launcher initializes MCP", "19 tools discovered", "Owned private desktop starts", "Native GTK callback confirms exact Unicode input", "Screenshot changed after acknowledged input"]
        client.tool("stop")
        client.close()
        assert client.process.returncode == 0, client.process.returncode
        report["checks"].append("Launcher exits cleanly after stop")
        report["status"] = "passed"
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        client.close()
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
