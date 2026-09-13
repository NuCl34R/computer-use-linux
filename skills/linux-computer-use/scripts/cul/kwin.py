"""KWin backend, used only inside a controller-owned private compositor."""
import struct
import time

from .wayland import Wayland, DataControl, uint, fixed, string, read_string
from .capture import PipeWireCapture
from .keys import keycodes


class KWinPrivate:
    name = "kwin-private"

    def __init__(self, width=1280, height=800):
        self.wl = Wayland()
        self.input = self.wl.bind("org_kde_kwin_fake_input", 4)
        self.wl.send(self.input, 0, string("Linux Computer Use") + string("Controller-owned isolated desktop"))
        self.clipboard = DataControl(self.wl)
        output = self.wl.bind("wl_output", 1)
        cast = self.wl.bind("zkde_screencast_unstable_v1", 1)
        self.node = None
        self.stream_error = None
        self.stream = self.wl.new(self._stream_event)
        self.wl.send(cast, 0, uint(self.stream, output, 1))
        self.wl.until(lambda: self.node is not None or self.stream_error)
        if self.stream_error:
            raise RuntimeError(self.stream_error)
        self.captures = [PipeWireCapture(self.node)]
        self.displays = [{"id": 0, "name": "Private desktop", "width": width, "height": height,
                          "x": 0, "y": 0, "node": self.node}]
        self.pressed = set()

    def _stream_event(self, op, data):
        if op == 1:
            self.node, = struct.unpack_from("=I", data)
        elif op == 2:
            self.stream_error, _ = read_string(data)
        elif op == 0:
            self.stream_error = "KWin closed its screencast"

    def check(self):
        self.wl.dispatch()
        if self.stream_error:
            raise RuntimeError(self.stream_error)

    def move(self, x, y, display=0):
        self.wl.send(self.input, 9, fixed(x, y))

    def button(self, code, down):
        self.wl.send(self.input, 2, uint(code, int(down)))
        if down:
            self.pressed.add(("button", code))
        else:
            self.pressed.discard(("button", code))

    def key(self, code, down):
        self.wl.send(self.input, 10, uint(code, int(down)))
        if down:
            self.pressed.add(("key", code))
        else:
            self.pressed.discard(("key", code))

    def press(self, chord):
        keys = keycodes(chord)
        try:
            for code in keys:
                self.key(code, True)
            self.wl.roundtrip()
        finally:
            for code in reversed(keys):
                self.key(code, False)
            self.wl.roundtrip()

    def type_text(self, text, paste_key="Ctrl+v"):
        if not text:
            return
        before = self.clipboard.transfers
        self.clipboard.set(text)
        self.press(paste_key)
        self.wl.until(lambda: self.clipboard.transfers > before, timeout=3)

    def scroll(self, dx, dy):
        if dx:
            self.wl.send(self.input, 3, uint(1) + fixed(dx))
        if dy:
            self.wl.send(self.input, 3, uint(0) + fixed(dy))
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
            for capture in self.captures:
                capture.close()
            self.wl.close()
