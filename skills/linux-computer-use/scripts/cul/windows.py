"""Compositor window discovery/focus. Never guess windows from XWayland only."""
import json
import os
import pathlib
import socket
import struct
import tempfile
import threading
import uuid

import dbus
import dbus.service
from .dbusutil import session_bus, close_bus


class Windows:
    def __init__(self):
        self.bus = session_bus()
        self.desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()

    def _kwin(self, window_id=None):
        event = threading.Event()
        results = []
        owner = str(self.bus.get_name_owner("org.kde.KWin"))
        name = "cul_" + uuid.uuid4().hex
        path = "/computer_use/" + name

        class Callback(dbus.service.Object):
            @dbus.service.method("local.ComputerUse.Callback", in_signature="s", out_signature="", sender_keyword="sender")
            def Result(self, value, sender=None):
                if sender == owner:
                    results.append(json.loads(str(value)))
                    event.set()

        callback = Callback(self.bus, path)
        service = self.bus.get_unique_name()
        script = """
            (function() {
                try {
                    const all = typeof workspace.windowList === 'function' ? workspace.windowList() : workspace.clientList();
                    const target = TARGET;
                    if (target !== null) {
                        const w = all.find(w => String(w.internalId) === target);
                        if (!w) throw new Error('Window no longer exists');
                        w.minimized = false;
                        if (w.desktops && w.desktops.length) workspace.currentDesktop = w.desktops[0];
                        if ('activeWindow' in workspace) workspace.activeWindow = w; else workspace.activeClient = w;
                    }
                    const windows = all.filter(w => w.normalWindow || w.dialog).map(w => ({
                        id: String(w.internalId), title: w.caption, app_id: String(w.resourceClass), pid: w.pid,
                        focused: w === ('activeWindow' in workspace ? workspace.activeWindow : workspace.activeClient),
                        minimized: w.minimized, bounds: {x:w.frameGeometry.x,y:w.frameGeometry.y,width:w.frameGeometry.width,height:w.frameGeometry.height}
                    }));
                    callDBus(SERVICE, PATH, 'local.ComputerUse.Callback', 'Result', JSON.stringify({windows: windows}));
                } catch(e) { callDBus(SERVICE, PATH, 'local.ComputerUse.Callback', 'Result', JSON.stringify({error:String(e)})); }
            })();
        """.replace("TARGET", json.dumps(window_id)).replace("SERVICE", json.dumps(service)).replace("PATH", json.dumps(path))
        fd, filename = tempfile.mkstemp(prefix="cul-window-", suffix=".js")
        with os.fdopen(fd, "w") as f:
            f.write(script)
        interface = dbus.Interface(self.bus.get_object("org.kde.KWin", "/Scripting"), "org.kde.kwin.Scripting")
        try:
            # Qt exposes overloaded loadScript(s) and loadScript(ss).
            # dbus-python's introspection cache cannot distinguish overloads.
            self.bus.call_blocking("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting",
                                   "loadScript", "ss", (filename, name), timeout=3)
            interface.start(timeout=3)
            if not event.wait(3):
                raise TimeoutError("KWin window query timed out")
            if "error" in results[0]:
                raise RuntimeError(results[0]["error"])
            return results[0]["windows"]
        finally:
            try:
                interface.unloadScript(name, timeout=2)
            finally:
                callback.remove_from_connection()
                os.unlink(filename)

    def _hypr(self, command):
        runtime = pathlib.Path(os.environ["XDG_RUNTIME_DIR"]) / "hypr"
        signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        paths = [runtime / signature / ".socket.sock"] if signature else list(runtime.glob("*/.socket.sock"))
        if len(paths) != 1:
            raise RuntimeError("Cannot select one Hyprland instance; set HYPRLAND_INSTANCE_SIGNATURE")
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(3)
            sock.connect(str(paths[0]))
            sock.sendall(command.encode())
            chunks, size = [], 0
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > 4 * 1024 * 1024:
                    raise RuntimeError("Hyprland IPC response too large")
                chunks.append(chunk)
            return b"".join(chunks).decode()

    def _sway(self, kind, message=""):
        path = os.environ.get("SWAYSOCK")
        if not path:
            paths = list(pathlib.Path(os.environ["XDG_RUNTIME_DIR"]).glob("sway-ipc.*.sock"))
            if len(paths) != 1:
                raise RuntimeError("Cannot select one Sway instance")
            path = str(paths[0])
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(3)
            sock.connect(path)
            payload = message.encode()
            sock.sendall(b"i3-ipc" + struct.pack("=II", len(payload), kind) + payload)
            def read(size):
                out = bytearray()
                while len(out) < size:
                    part = sock.recv(size - len(out))
                    if not part:
                        raise ConnectionError("Sway IPC disconnected")
                    out.extend(part)
                return out
            header = read(14)
            length, _ = struct.unpack_from("=II", header, 6)
            if header[:6] != b"i3-ipc" or length > 4 * 1024 * 1024:
                raise RuntimeError("Invalid Sway IPC response")
            return json.loads(read(length))

    def list(self):
        if "kde" in self.desktop or "plasma" in self.desktop:
            return self._kwin()
        if "hyprland" in self.desktop:
            active = json.loads(self._hypr("j/activewindow"))
            return [{"id": w["address"], "title": w["title"], "pid": w["pid"], "app_id": w["class"],
                     "focused": w["address"] == active.get("address"),
                     "bounds": dict(zip(("x", "y", "width", "height"), w["at"] + w["size"]))}
                    for w in json.loads(self._hypr("j/clients"))]
        if "sway" in self.desktop:
            result, pending = [], [self._sway(4)]
            while pending:
                node = pending.pop()
                if node.get("pid"):
                    result.append({"id": str(node["id"]), "title": node.get("name"), "pid": node["pid"],
                        "app_id": node.get("app_id"), "focused": node["focused"], "bounds": node["rect"]})
                pending.extend(node.get("nodes", []) + node.get("floating_nodes", []))
            return result
        if os.environ.get("XDG_SESSION_TYPE") == "x11":
            from .x11 import X11
            backend = X11(capture=False)
            try:
                return backend.windows()
            finally:
                backend.close()
        raise RuntimeError("Exact window discovery unavailable for this compositor; use AT-SPI apps/state")

    def focus(self, window_id):
        if not any(w["id"] == str(window_id) for w in self.list()):
            raise ValueError("Unknown window id; refresh windows")
        if "kde" in self.desktop or "plasma" in self.desktop:
            self._kwin(str(window_id))
        elif "hyprland" in self.desktop:
            answer = self._hypr("dispatch focuswindow address:" + str(window_id))
            if answer.strip() != "ok":
                raise RuntimeError(answer)
        elif "sway" in self.desktop:
            result = self._sway(0, f"[con_id={int(window_id)}] focus")
            if not all(r.get("success") for r in result):
                raise RuntimeError(str(result))
        else:
            from .x11 import X11
            backend = X11(capture=False)
            try:
                backend.focus(int(window_id))
            finally:
                backend.close()
        return {"focused": str(window_id)}

    def close(self):
        close_bus(self.bus)
