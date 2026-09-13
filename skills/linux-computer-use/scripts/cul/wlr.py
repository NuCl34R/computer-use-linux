"""Hyprland/wlroots: virtual input and shared-memory screencopy protocols."""
import ctypes
import ctypes.util
import mmap
import os
import struct
import time

from .wayland import Wayland, DataControl, uint, sint, fixed, read_string
from .capture import encode, GdkPixbuf, GLib
from .keys import keycodes


def xkb_keymap():
    lib = ctypes.CDLL(ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0")
    for name, result, args in (
        ("xkb_context_new", ctypes.c_void_p, [ctypes.c_int]),
        ("xkb_keymap_new_from_names", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]),
        ("xkb_keymap_get_as_string", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_int]),
        ("xkb_keymap_unref", None, [ctypes.c_void_p]),
        ("xkb_context_unref", None, [ctypes.c_void_p])):
        function = getattr(lib, name)
        function.restype, function.argtypes = result, args
    class Names(ctypes.Structure):
        _fields_ = [(k, ctypes.c_char_p) for k in ("rules", "model", "layout", "variant", "options")]
    context = lib.xkb_context_new(0)
    keymap = lib.xkb_keymap_new_from_names(context, ctypes.byref(Names(b"evdev", b"pc105", b"us", b"", b"")), 0)
    if not keymap:
        lib.xkb_context_unref(context)
        raise RuntimeError("Cannot create virtual keyboard keymap; install xkeyboard-config")
    pointer = lib.xkb_keymap_get_as_string(keymap, 1)
    data = ctypes.string_at(pointer) + b"\0"
    libc = ctypes.CDLL(None)
    libc.free.argtypes = [ctypes.c_void_p]
    libc.free(pointer)
    lib.xkb_keymap_unref(keymap)
    lib.xkb_context_unref(context)
    return data


class WlrCapture:
    def __init__(self, wl, output, transform=0):
        self.wl, self.output, self.transform = wl, output, transform
        self.manager = wl.bind("zwlr_screencopy_manager_v1", 1)
        self.shm = wl.bind("wl_shm", 1)
        self.sequence = 0

    def snapshot(self, max_width=1600, max_height=1000, format="jpeg", quality=85, after=None):
        result = {}
        resources = {}

        def event(opcode, data):
            if opcode == 0:
                fmt, width, height, stride = struct.unpack("=IIII", data)
                size = height * stride
                if width > 16384 or height > 16384 or stride < width * 4 or size > 256 * 1024 * 1024:
                    raise RuntimeError("Unreasonable compositor buffer dimensions")
                fd = os.memfd_create("cul-frame", os.MFD_CLOEXEC)
                resources["fd"] = fd
                os.ftruncate(fd, size)
                resources["map"] = mmap.mmap(fd, size)
                pool = resources["pool"] = self.wl.new()
                buffer = resources["buffer"] = self.wl.new()
                self.wl.send(self.shm, 0, uint(pool) + sint(size), [fd])
                self.wl.send(pool, 0, uint(buffer) + sint(0, width, height, stride) + uint(fmt))
                result.update(width=width, height=height, stride=stride, format=fmt)
                self.wl.send(frame, 0, uint(buffer))
            elif opcode == 1:
                result["flags"], = struct.unpack("=I", data)
            elif opcode == 2:
                result["ready"] = True
            elif opcode == 3:
                result["failed"] = True

        frame = self.wl.new(event)
        try:
            self.wl.send(self.manager, 0, uint(frame, 0, self.output))
            self.wl.until(lambda: result.get("ready") or result.get("failed"), 5)
            if result.get("failed"):
                raise RuntimeError("Compositor refused screencopy")
            width, height, stride, fmt = (result[k] for k in ("width", "height", "stride", "format"))
            raw = resources["map"][:]
            if stride != width * 4:
                raw = b"".join(raw[y * stride:y * stride + width * 4] for y in range(height))
            rgb = bytearray(width * height * 3)
            if fmt in (0, 1):  # ARGB8888 / XRGB8888, little-endian BGRA
                rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
            elif fmt in (0x34324241, 0x34324258):
                rgb[0::3], rgb[1::3], rgb[2::3] = raw[0::4], raw[1::4], raw[2::4]
            else:
                raise RuntimeError(f"Unsupported screencopy SHM pixel format {fmt:#x}")
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(rgb)), GdkPixbuf.Colorspace.RGB,
                                                   False, 8, width, height, width * 3)
            if result.get("flags", 0) & 1:
                pixbuf = pixbuf.flip(False)
            if self.transform >= 4:
                pixbuf = pixbuf.flip(True)
            rotation = self.transform % 4
            if rotation:
                pixbuf = pixbuf.rotate_simple({1: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE,
                    2: GdkPixbuf.PixbufRotation.UPSIDEDOWN, 3: GdkPixbuf.PixbufRotation.CLOCKWISE}[rotation])
            self.sequence += 1
            return encode(pixbuf, max_width, max_height, format, quality) | {
                "capture_width": pixbuf.get_width(), "capture_height": pixbuf.get_height(),
                "sequence": self.sequence, "age_ms": 0, "after_action_frame": True}
        finally:
            self.wl.send(frame, 1)
            if "buffer" in resources:
                self.wl.send(resources["buffer"], 0)
            if "pool" in resources:
                self.wl.send(resources["pool"], 1)
            if "map" in resources:
                resources["map"].close()
            if "fd" in resources:
                os.close(resources["fd"])

    def close(self):
        pass


class Wlr:
    name = "wlr"

    def __init__(self):
        self.wl = Wayland()
        self.displays, self.outputs = [], []
        for index, global_ in enumerate(g for g in self.wl.globals if g[1] == "wl_output"):
            display = {"id": index, "name": f"Output {index + 1}", "x": 0, "y": 0, "scale": 1, "transform": 0}
            def event(op, data, display=display):
                if op == 0:
                    display["x"], display["y"] = struct.unpack_from("=ii", data)
                    _, pos = read_string(data, 20)
                    _, pos = read_string(data, pos)
                    display["transform"], = struct.unpack_from("=i", data, pos)
                elif op == 1:
                    flags, w, h, _ = struct.unpack("=Iiii", data)
                    if flags & 1:
                        display.update(pixel_width=w, pixel_height=h)
                elif op == 3:
                    display["scale"], = struct.unpack("=i", data)
                elif op == 4:
                    display["name"], _ = read_string(data)
            self.outputs.append(self.wl.bind("wl_output", 4, event, index=index))
            self.displays.append(display)
        self.wl.roundtrip()
        # xdg-output reports the real logical dimensions for fractional scaling.
        if any(g[1] == "zxdg_output_manager_v1" for g in self.wl.globals):
            manager = self.wl.bind("zxdg_output_manager_v1", 2)
            for output, display in zip(self.outputs, self.displays):
                def event(op, data, display=display):
                    if op in (0, 1):
                        a, b = struct.unpack("=ii", data)
                        display.update(dict(zip(("x", "y") if op == 0 else ("width", "height"), (a, b))))
                obj = self.wl.new(event)
                self.wl.send(manager, 1, uint(obj, output))
            self.wl.roundtrip()
        for display in self.displays:
            if "width" not in display:
                w, h = display["pixel_width"], display["pixel_height"]
                if display["transform"] % 2:
                    w, h = h, w
                display.update(width=w // display["scale"], height=h // display["scale"])
        if not self.displays:
            raise RuntimeError("No Wayland output available")
        self.captures = [WlrCapture(self.wl, out, d["transform"]) for out, d in zip(self.outputs, self.displays)]
        seat = self.wl.bind("wl_seat", 1)
        pointer_manager = self.wl.bind("zwlr_virtual_pointer_manager_v1", 2)
        pointer_version = next(g[2] for g in self.wl.globals if g[1] == "zwlr_virtual_pointer_manager_v1")
        self.pointers = []
        for output in self.outputs:
            pointer = self.wl.new()
            if pointer_version >= 2:
                self.wl.send(pointer_manager, 2, uint(seat, output, pointer))
            else:
                if len(self.outputs) != 1:
                    raise RuntimeError("Virtual pointer v1 cannot target multiple outputs precisely")
                self.wl.send(pointer_manager, 0, uint(seat, pointer))
            self.pointers.append(pointer)
        self.pointer = self.pointers[0]
        keyboard_manager = self.wl.bind("zwp_virtual_keyboard_manager_v1", 1)
        self.keyboard = self.wl.new()
        self.wl.send(keyboard_manager, 0, uint(seat, self.keyboard))
        keymap = xkb_keymap()
        fd = os.memfd_create("cul-keymap", os.MFD_CLOEXEC)
        try:
            os.write(fd, keymap)
            self.wl.send(self.keyboard, 0, uint(1, len(keymap)), [fd])
        finally:
            os.close(fd)
        self.clipboard = DataControl(self.wl)
        self.pressed = set()
        self.wl.roundtrip()

    @staticmethod
    def timestamp():
        return int(time.monotonic() * 1000) & 0xffffffff

    def check(self):
        self.wl.dispatch()

    def move(self, x, y, display=0):
        self.pointer = self.pointers[display]
        d = self.displays[display]
        self.wl.send(self.pointer, 1, uint(self.timestamp(), round(x * 256), round(y * 256), d["width"] * 256, d["height"] * 256))
        self.wl.send(self.pointer, 4)

    def button(self, code, down):
        self.wl.send(self.pointer, 2, uint(self.timestamp(), code, int(down)))
        self.wl.send(self.pointer, 4)
        (self.pressed.add if down else self.pressed.discard)(("button", code))

    def key(self, code, down):
        self.wl.send(self.keyboard, 1, uint(self.timestamp(), code, int(down)))
        (self.pressed.add if down else self.pressed.discard)(("key", code))
        modifiers = sum(mask for key, mask in ((42, 1), (29, 4), (56, 8), (125, 64)) if ("key", key) in self.pressed)
        self.wl.send(self.keyboard, 2, uint(modifiers, 0, 0, 0))

    def press(self, chord):
        codes = keycodes(chord)
        try:
            for code in codes:
                self.key(code, True)
            self.wl.roundtrip()
        finally:
            for code in reversed(codes):
                self.key(code, False)
            self.wl.roundtrip()

    def type_text(self, text, paste_key="Ctrl+v"):
        if text:
            before = self.clipboard.transfers
            self.clipboard.set(text)
            self.press(paste_key)
            self.wl.until(lambda: self.clipboard.transfers > before, 3)

    def scroll(self, dx, dy):
        self.wl.send(self.pointer, 5, uint(0))  # wheel
        for axis, amount in ((0, dy), (1, dx)):
            if amount:
                self.wl.send(self.pointer, 7, uint(self.timestamp(), axis) + fixed(amount) + sint(round(amount / 15) or (1 if amount > 0 else -1)))
        self.wl.send(self.pointer, 4)
        self.wl.roundtrip()

    def sync(self):
        self.wl.roundtrip()

    def release(self):
        for kind, code in list(self.pressed):
            getattr(self, kind)(code, False)
        self.sync()

    def close(self):
        try:
            self.release()
        finally:
            self.wl.close()
