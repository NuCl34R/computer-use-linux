"""Standard RemoteDesktop + ScreenCast portal, one session for all devices."""
import os
import pathlib
import select
import threading
import time
import uuid
import dbus

from .capture import PipeWireCapture
from .dbusutil import session_bus, plain, close_bus
from .keys import names, keysym

SERVICE = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
PREFIX = "org.freedesktop.portal."


class Portal:
    name = "portal"

    def __init__(self, token_path=None, timeout=120):
        self.bus = session_bus()
        self.identity_registered = False
        try:
            self.bus.call_blocking(SERVICE, PATH, "org.freedesktop.host.portal.Registry", "Register",
                "sa{sv}", ("local.linuxcomputeruse.Controller", {}), timeout=5)
            self.identity_registered = True
        except dbus.DBusException:
            # Older portals do not support explicit host app registration.
            pass
        self.object = self.bus.get_object(SERVICE, PATH)
        self.remote = dbus.Interface(self.object, PREFIX + "RemoteDesktop")
        self.cast = dbus.Interface(self.object, PREFIX + "ScreenCast")
        self.clip = dbus.Interface(self.object, PREFIX + "Clipboard")
        self.timeout = timeout
        self.session = None
        self.valid = False
        self.captures = []
        self.fds = []
        self.matches = []
        self.pressed = set()
        self.clipboard_enabled = False
        self.clipboard_data = b""
        self.wayland_clipboard = None
        self.transfer_done = threading.Event()
        self.token_path = pathlib.Path(token_path) if token_path else None
        try:
            result = self._request(self.remote, "CreateSession", options={"session_handle_token": "cul_" + uuid.uuid4().hex})
            self.session = dbus.ObjectPath(result["session_handle"])
            self.matches.append(self.bus.add_signal_receiver(self._closed, signal_name="Closed",
                dbus_interface=PREFIX + "Session", bus_name=SERVICE, path=self.session))
            properties = dbus.Interface(self.object, "org.freedesktop.DBus.Properties")
            version = int(properties.Get(PREFIX + "RemoteDesktop", "version"))
            options = {"types": dbus.UInt32(3)}
            if version >= 2 and self.token_path:
                options["persist_mode"] = dbus.UInt32(2)
                if self.token_path.exists():
                    options["restore_token"] = self.token_path.read_text().strip()
                    # A restore token is single-use. Never retry a consumed one.
                    self.token_path.unlink()
            self._request(self.remote, "SelectDevices", self.session, options=options)
            self._request(self.cast, "SelectSources", self.session, options={
                "types": dbus.UInt32(1), "multiple": dbus.Boolean(True), "cursor_mode": dbus.UInt32(1)})
            try:
                self.clip.RequestClipboard(self.session, {}, timeout=5)
                self.matches.append(self.bus.add_signal_receiver(self._transfer, signal_name="SelectionTransfer",
                    dbus_interface=PREFIX + "Clipboard", bus_name=SERVICE, path=PATH))
            except dbus.DBusException:
                pass
            result = self._request(self.remote, "Start", self.session, "", options={})
            if int(result.get("devices", 0)) & 3 != 3:
                raise RuntimeError("Portal did not grant both keyboard and pointer")
            if not result.get("streams"):
                raise RuntimeError("Portal did not grant any monitor stream")
            self.clipboard_enabled = bool(result.get("clipboard_enabled", False))
            if not self.clipboard_enabled and os.environ.get("WAYLAND_DISPLAY"):
                # KDE versions without Clipboard portal still expose the
                # standard data-control protocol to local clipboard managers.
                from .wayland import Wayland, DataControl
                wl = None
                try:
                    wl = Wayland()
                    self.wayland_clipboard = DataControl(wl)
                except Exception:
                    if wl:
                        wl.close()
            if result.get("restore_token") and self.token_path:
                self.token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(str(result["restore_token"]))
            self.displays = []
            for index, (node, props) in enumerate(result["streams"]):
                size = props.get("logical_size", props.get("size"))
                if not size or size[0] <= 0 or size[1] <= 0:
                    raise RuntimeError("Portal omitted logical monitor size; refusing to guess input coordinates")
                position = props.get("position", (0, 0))
                self.displays.append({"id": index, "name": str(props.get("id", f"Monitor {index + 1}")),
                    "node": int(node), "width": int(size[0]), "height": int(size[1]),
                    "x": int(position[0]), "y": int(position[1])})
                fd = self.cast.OpenPipeWireRemote(self.session, {}, timeout=5).take()
                self.fds.append(fd)
                self.captures.append(PipeWireCapture(node, fd))
            self.valid = True
        except BaseException:
            self.close()
            raise

    def _request(self, interface, method, *args, options):
        token = "cul_" + uuid.uuid4().hex
        options = dict(options, handle_token=token)
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        expected = PATH + "/request/" + sender + "/" + token
        event = threading.Event()
        response = []

        def received(code, results):
            response.append((int(code), results))
            event.set()

        match = self.bus.add_signal_receiver(received, signal_name="Response",
            dbus_interface=PREFIX + "Request", bus_name=SERVICE, path=expected)
        try:
            actual = getattr(interface, method)(*args, dbus.Dictionary(options, signature="sv"), timeout=10)
            if str(actual) != expected:
                raise RuntimeError("Portal returned an unexpected request handle")
            if not event.wait(self.timeout):
                try:
                    self.bus.get_object(SERVICE, actual).Close(dbus_interface=PREFIX + "Request", timeout=2)
                finally:
                    raise TimeoutError(f"Portal {method} timed out waiting for desktop consent")
            code, result = response[0]
            if code:
                raise PermissionError(f"Portal {method} {'cancelled by user' if code == 1 else 'failed'} (response {code})")
            return result
        finally:
            match.remove()

    def _closed(self, *args):
        self.valid = False

    def check(self):
        if not self.valid:
            raise RuntimeError("Portal access has ended or been revoked; start a new session")

    def move(self, x, y, display=0):
        self.check()
        self.remote.NotifyPointerMotionAbsolute(self.session, {}, dbus.UInt32(self.displays[display]["node"]),
                                                float(x), float(y), timeout=3)

    def button(self, code, down):
        self.check()
        self.remote.NotifyPointerButton(self.session, {}, dbus.Int32(code), dbus.UInt32(down), timeout=3)
        (self.pressed.add if down else self.pressed.discard)(("button", code))

    def key(self, code, down):
        self.check()
        self.remote.NotifyKeyboardKeysym(self.session, {}, dbus.Int32(code), dbus.UInt32(down), timeout=3)
        (self.pressed.add if down else self.pressed.discard)(("key", code))

    def press(self, chord):
        codes = [keysym(name) for name in names(chord)]
        pressed = []
        try:
            for code in codes:
                self.key(code, True)
                pressed.append(code)
        finally:
            for code in reversed(pressed):
                self.key(code, False)

    def _transfer(self, session, mime, serial):
        if session != self.session:
            return
        # Do not block GLib's signal-dispatch thread on a pipe consumer.
        def write():
            success = False
            try:
                fd = self.clip.SelectionWrite(self.session, serial, timeout=3).take()
                try:
                    os.set_blocking(fd, False)
                    view = memoryview(self.clipboard_data)
                    deadline = time.monotonic() + 3
                    while view:
                        if time.monotonic() > deadline:
                            raise TimeoutError("Clipboard transfer timed out")
                        if select.select([], [fd], [], .1)[1]:
                            view = view[os.write(fd, view):]
                    success = True
                finally:
                    os.close(fd)
            finally:
                self.clip.SelectionWriteDone(self.session, serial, success, timeout=3)
                if success:
                    self.transfer_done.set()
        threading.Thread(target=write, daemon=True).start()

    def type_text(self, text, paste_key="Ctrl+v"):
        self.check()
        if not text:
            return
        if self.clipboard_enabled:
            self.clipboard_data = text.encode("utf-8")
            self.transfer_done.clear()
            self.clip.SetSelection(self.session, {"mime_types": dbus.Array(
                ["text/plain;charset=utf-8", "text/plain", "UTF8_STRING"], signature="s")}, timeout=3)
            self.press(paste_key)
            if not self.transfer_done.wait(3):
                raise TimeoutError("Target did not consume clipboard; check focus and paste_key")
        elif self.wayland_clipboard:
            clipboard = self.wayland_clipboard
            before = clipboard.transfers
            clipboard.set(text)
            self.press(paste_key)
            clipboard.wl.until(lambda: clipboard.transfers > before, 3)
        else:
            if any(ord(char) > 127 for char in text):
                raise RuntimeError("This portal grants no clipboard and the compositor has no data-control protocol; use AT-SPI set_value for Unicode instead of silently losing characters")
            for char in text:
                code = keysym({"\n": "Return", "\t": "Tab"}.get(char, char))
                try:
                    self.key(code, True)
                finally:
                    self.key(code, False)

    def scroll(self, dx, dy):
        self.check()
        # Discrete wheel events are supported across KDE/Mutter versions;
        # older KDE portal paths silently drop continuous-axis notifications.
        for axis, amount in ((0, dy), (1, dx)):
            if amount:
                steps = round(amount / 15) or (1 if amount > 0 else -1)
                self.remote.NotifyPointerAxisDiscrete(self.session, {}, dbus.UInt32(axis), dbus.Int32(steps), timeout=3)

    def sync(self):
        self.check()

    def release(self):
        if self.valid:
            for kind, code in list(self.pressed):
                getattr(self, kind)(code, False)

    def close(self):
        try:
            self.release()
        except Exception:
            pass
        self.valid = False
        for capture in self.captures:
            capture.close()
        for fd in self.fds:
            os.close(fd)
        self.fds.clear()
        if self.wayland_clipboard:
            self.wayland_clipboard.wl.close()
            self.wayland_clipboard = None
        if self.session:
            try:
                self.bus.get_object(SERVICE, self.session).Close(dbus_interface=PREFIX + "Session", timeout=2)
            except Exception:
                pass
            self.session = None
        for match in self.matches:
            match.remove()
        self.matches.clear()
        close_bus(self.bus)
