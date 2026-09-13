"""X11/Xvfb via persistent Xlib/XTEST connections; never selected on Wayland."""
import ctypes as C
import ctypes.util
import os
import threading
import time

from .capture import encode, GdkPixbuf, GLib
from .keys import names, keysym

P = C.c_void_p
U = C.c_ulong
I = C.c_int
_ERROR_HANDLER = C.CFUNCTYPE(I, P, P)(lambda display, event: 0)


class XImage(C.Structure):
    _fields_ = [(n, I) for n in ("width", "height", "xoffset", "format")] + [("data", P)] + [
        (n, I) for n in ("byte_order", "bitmap_unit", "bitmap_bit_order", "bitmap_pad", "depth", "bytes_per_line", "bits_per_pixel")] + [
        (n, U) for n in ("red_mask", "green_mask", "blue_mask")] + [("obdata", P), ("functions", P * 6)]


class Attributes(C.Structure):
    _fields_ = [(n, I) for n in ("x", "y", "width", "height", "border_width", "depth")] + [
        ("visual", P), ("root", U)] + [(n, I) for n in ("class_", "bit_gravity", "win_gravity", "backing_store")] + [
        ("backing_planes", U), ("backing_pixel", U), ("save_under", I), ("colormap", U), ("map_installed", I),
        ("map_state", I), ("all_event_masks", C.c_long), ("your_event_mask", C.c_long),
        ("do_not_propagate_mask", C.c_long), ("override_redirect", I), ("screen", P)]


class X11:
    name = "x11"

    def __init__(self, capture=True):
        self.x = C.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        self.xtest = C.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
        for name, result, args in (
            ("XOpenDisplay", P, [C.c_char_p]), ("XDefaultRootWindow", U, [P]),
            ("XDefaultScreen", I, [P]), ("XDisplayWidth", I, [P, I]), ("XDisplayHeight", I, [P, I]),
            ("XCloseDisplay", I, [P]), ("XFlush", I, [P]), ("XSync", I, [P, I]),
            ("XKeysymToKeycode", C.c_ubyte, [P, U]),
            ("XGetImage", C.POINTER(XImage), [P, U, I, I, C.c_uint, C.c_uint, U, I]),
            ("XDestroyImage", I, [C.POINTER(XImage)]),
            ("XQueryTree", I, [P, U, C.POINTER(U), C.POINTER(U), C.POINTER(C.POINTER(U)), C.POINTER(C.c_uint)]),
            ("XGetWindowAttributes", I, [P, U, C.POINTER(Attributes)]),
            ("XFetchName", I, [P, U, C.POINTER(P)]), ("XFree", I, [P]),
            ("XInternAtom", U, [P, C.c_char_p, I]),
            ("XGetWindowProperty", I, [P, U, U, C.c_long, C.c_long, I, U, C.POINTER(U), C.POINTER(I), C.POINTER(U), C.POINTER(U), C.POINTER(P)]),
            ("XRaiseWindow", I, [P, U]), ("XSetInputFocus", I, [P, U, I, U]),
            ("XTranslateCoordinates", I, [P, U, U, I, I, C.POINTER(I), C.POINTER(I), C.POINTER(U)])):
            fn = getattr(self.x, name)
            fn.restype, fn.argtypes = result, args
        self.x.XInitThreads()
        self.display = self.x.XOpenDisplay(os.environ.get("DISPLAY", "").encode())
        if not self.display:
            raise RuntimeError("Cannot connect to the selected X display")
        # Avoid libX11's default process-terminating handler on windows that
        # disappear during enumeration. Each query checks its return status.
        self.x.XSetErrorHandler.argtypes = [C.CFUNCTYPE(I, P, P)]
        self.x.XSetErrorHandler(_ERROR_HANDLER)
        for name, args in (("XTestFakeMotionEvent", [P, I, I, I, U]),
                           ("XTestFakeRelativeMotionEvent", [P, I, I, U]),
                           ("XTestFakeButtonEvent", [P, C.c_uint, I, U]),
                           ("XTestFakeKeyEvent", [P, C.c_uint, I, U])):
            getattr(self.xtest, name).argtypes = args
            getattr(self.xtest, name).restype = I
        self.root = self.x.XDefaultRootWindow(self.display)
        self.screen = self.x.XDefaultScreen(self.display)
        self.width = self.x.XDisplayWidth(self.display, self.screen)
        self.height = self.x.XDisplayHeight(self.display, self.screen)
        self.displays = [{"id": 0, "name": "X11 desktop", "width": self.width, "height": self.height, "x": 0, "y": 0}]
        self.captures = [self] if capture else []
        self.sequence = 0
        self.pressed = set()
        self.clipboard = None

    def check(self):
        if not self.display:
            raise RuntimeError("X display is closed")

    def snapshot(self, max_width=1600, max_height=1000, format="jpeg", quality=85, after=None):
        self.check()
        ptr = self.x.XGetImage(self.display, self.root, 0, 0, self.width, self.height, U(-1), 2)
        if not ptr:
            raise RuntimeError("XGetImage failed")
        try:
            info = ptr.contents
            if info.bits_per_pixel != 32 or info.byte_order != 0 or (info.red_mask, info.green_mask, info.blue_mask) != (0xff0000, 0xff00, 0xff):
                raise RuntimeError("Unsupported X visual; use a 24-bit Xvfb screen")
            raw = C.string_at(info.data, info.bytes_per_line * info.height)
            if info.bytes_per_line != info.width * 4:
                raw = b"".join(raw[y * info.bytes_per_line:y * info.bytes_per_line + info.width * 4] for y in range(info.height))
            rgb = bytearray(info.width * info.height * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(rgb)), GdkPixbuf.Colorspace.RGB,
                False, 8, info.width, info.height, info.width * 3)
            self.sequence += 1
            return encode(pixbuf, max_width, max_height, format, quality) | {"capture_width": self.width,
                "capture_height": self.height, "sequence": self.sequence, "age_ms": 0, "after_action_frame": True}
        finally:
            self.x.XDestroyImage(ptr)

    def move(self, x, y, display=0):
        self.xtest.XTestFakeMotionEvent(self.display, self.screen, round(x), round(y), 0)
        self.sync()

    def move_relative(self, dx, dy):
        self.xtest.XTestFakeRelativeMotionEvent(self.display, round(dx), round(dy), 0)
        self.sync()

    def button(self, code, down):
        mapping = {272: 1, 273: 3, 274: 2, 275: 8, 276: 9}
        if code not in mapping:
            raise ValueError("Unsupported X11 pointer button")
        self.xtest.XTestFakeButtonEvent(self.display, mapping[code], down, 0)
        (self.pressed.add if down else self.pressed.discard)(("button", code))
        self.sync()

    def key(self, code, down):
        self.xtest.XTestFakeKeyEvent(self.display, code, down, 0)
        (self.pressed.add if down else self.pressed.discard)(("key", code))
        self.sync()

    def press(self, chord):
        codes = [self.x.XKeysymToKeycode(self.display, keysym(name)) for name in names(chord)]
        if not all(codes):
            raise ValueError("Key unavailable in the active XKB layout")
        try:
            for code in codes:
                self.key(code, True)
        finally:
            for code in reversed(codes):
                self.key(code, False)

    def type_text(self, text, paste_key="Ctrl+v"):
        if not text:
            return
        import gi
        gi.require_version("Gtk", "3.0")
        if self.clipboard is None:
            bridge = os.environ.get("NO_AT_BRIDGE")
            os.environ["NO_AT_BRIDGE"] = "1"
            try:
                # The controller is an AT-SPI client, not an accessible GTK
                # application. Loading its own ATK bridge would share and
                # later double-close libatspi's global D-Bus connection.
                from gi.repository import Gtk, Gdk, Gio
                if not Gtk.init_check()[0]:
                    raise RuntimeError("GTK could not connect to the X display for clipboard transfer")
                Gio.bus_get_sync(Gio.BusType.SESSION, None).set_exit_on_close(False)
                self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            finally:
                if bridge is None:
                    os.environ.pop("NO_AT_BRIDGE", None)
                else:
                    os.environ["NO_AT_BRIDGE"] = bridge
        self.clipboard.set_text(text, -1)
        self.press(paste_key)
        # GTK's selection owner is served by the controller's GLib event pump.
        time.sleep(.04)

    def scroll(self, dx, dy):
        for value, positive, negative in ((dy, 5, 4), (dx, 7, 6)):
            for _ in range(min(100, max(1, round(abs(value) / 15))) if value else 0):
                button = positive if value > 0 else negative
                self.xtest.XTestFakeButtonEvent(self.display, button, 1, 0)
                self.xtest.XTestFakeButtonEvent(self.display, button, 0, 0)
        self.sync()

    def sync(self):
        self.x.XSync(self.display, 0)

    def _property(self, window, name):
        atom = self.x.XInternAtom(self.display, name.encode(), 1)
        actual, fmt, count, remaining, data = U(), I(), U(), U(), P()
        if not atom or self.x.XGetWindowProperty(self.display, window, atom, 0, 4096, 0, 0,
                C.byref(actual), C.byref(fmt), C.byref(count), C.byref(remaining), C.byref(data)):
            return None
        try:
            if not data.value:
                return None
            if fmt.value == 32:
                return list(C.cast(data, C.POINTER(U))[:count.value])
            if fmt.value == 8:
                return C.string_at(data, count.value).decode("utf-8", errors="replace")
        finally:
            if data.value:
                self.x.XFree(data)

    def windows(self):
        windows = self._property(self.root, "_NET_CLIENT_LIST")
        if windows is None:
            root, parent, children, count = U(), U(), C.POINTER(U)(), C.c_uint()
            if not self.x.XQueryTree(self.display, self.root, C.byref(root), C.byref(parent), C.byref(children), C.byref(count)):
                return []
            windows = list(children[:min(count.value, 1000)])
            if children:
                self.x.XFree(children)
        active = self._property(self.root, "_NET_ACTIVE_WINDOW") or []
        result = []
        for window in windows[:1000]:
            attrs = Attributes()
            if not self.x.XGetWindowAttributes(self.display, window, C.byref(attrs)) or attrs.map_state != 2 or attrs.override_redirect:
                continue
            title = self._property(window, "_NET_WM_NAME") or self._property(window, "WM_NAME") or ""
            pid = self._property(window, "_NET_WM_PID") or [None]
            x, y, child = I(), I(), U()
            self.x.XTranslateCoordinates(self.display, window, self.root, 0, 0, C.byref(x), C.byref(y), C.byref(child))
            result.append({"id": str(window), "title": title, "pid": pid[0], "focused": window in active,
                "app_id": self._property(window, "WM_CLASS"), "bounds": {"x": x.value, "y": y.value, "width": attrs.width, "height": attrs.height}})
        return result

    def focus(self, window):
        self.x.XRaiseWindow(self.display, window)
        self.x.XSetInputFocus(self.display, window, 1, 0)
        self.sync()

    def release(self):
        for kind, code in list(self.pressed):
            getattr(self, kind)(code, False)

    def close(self):
        if self.display:
            self.release()
            if self.clipboard is not None:
                # GDK's default X I/O error handler exits the entire process if
                # its server dies. Close our clipboard connection before Xvfb.
                done = threading.Event()
                def disconnect():
                    try:
                        self.clipboard.clear()
                        display = self.clipboard.get_display()
                        # GTK destroys its cached Clipboard when the display
                        # closes. Drop the GI wrapper while that cache is live.
                        self.clipboard = None
                        display.close()
                    finally:
                        done.set()
                    return False
                GLib.idle_add(disconnect)
                done.wait(2)
            self.x.XCloseDisplay(self.display)
            self.display = None
