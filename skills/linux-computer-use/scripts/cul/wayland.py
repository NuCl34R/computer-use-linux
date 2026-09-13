"""Small Wayland wire client for the protocols used by our private desktop.

No generated binary, compiler, root service or Python package is required. All
messages are bounded; ancillary FDs are closed unless a handler takes ownership.
Protocol specifications, rather than compositor internals, define the opcodes.
"""
import array
import collections
import os
import select
import socket
import struct
import time


def uint(*values):
    return struct.pack("=" + "I" * len(values), *values)


def sint(*values):
    return struct.pack("=" + "i" * len(values), *values)


def fixed(*values):
    return sint(*(round(v * 256) for v in values))


def string(value):
    data = value.encode("utf-8") + b"\0"
    return uint(len(data)) + data + b"\0" * (-len(data) % 4)


def read_string(data, offset=0):
    length, = struct.unpack_from("=I", data, offset)
    if length > len(data) - offset - 4:
        raise RuntimeError("Malformed Wayland string")
    return data[offset + 4:offset + 3 + length].decode("utf-8"), offset + 4 + ((length + 3) & ~3)


class Wayland:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        display = env.get("WAYLAND_DISPLAY", "wayland-0")
        path = display if os.path.isabs(display) else os.path.join(env["XDG_RUNTIME_DIR"], display)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect(path)
        self.buffer = bytearray()
        self.fds = collections.deque()
        self.handlers = {1: self._display}
        self.next_id = 2
        self.globals = []
        self.registry = self.new(self._registry)
        self.send(1, 1, uint(self.registry))
        self.roundtrip()

    def new(self, handler=None):
        value = self.next_id
        self.next_id += 1
        self.handlers[value] = handler or (lambda op, data: None)
        return value

    def send(self, obj, opcode, data=b"", fds=()):
        size = len(data) + 8
        if size > 65535 or size % 4:
            raise ValueError("Invalid Wayland message size")
        message = uint(obj, (size << 16) | opcode) + data
        ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))] if fds else []
        sent = self.sock.sendmsg([message], ancillary)
        if sent < len(message):
            self.sock.sendall(message[sent:])

    def _display(self, opcode, data):
        if opcode == 0:
            obj, code = struct.unpack_from("=II", data)
            message, _ = read_string(data, 8)
            raise RuntimeError(f"Wayland error {code} on {obj}: {message}")
        if opcode == 1:
            self.handlers.pop(struct.unpack_from("=I", data)[0], None)

    def _registry(self, opcode, data):
        name, = struct.unpack_from("=I", data)
        if opcode == 0:
            interface, pos = read_string(data, 4)
            version, = struct.unpack_from("=I", data, pos)
            self.globals.append((name, interface, version))
        elif opcode == 1:
            self.globals = [g for g in self.globals if g[0] != name]

    def bind(self, interface, version=1, handler=None, index=0):
        matches = [g for g in self.globals if g[1] == interface]
        if index >= len(matches):
            raise RuntimeError(f"Compositor does not expose {interface}")
        name, _, offered = matches[index]
        obj = self.new(handler)
        self.send(self.registry, 0, uint(name) + string(interface) + uint(min(offered, version), obj))
        return obj

    def dispatch(self, timeout=0.0):
        if select.select([self.sock], [], [], timeout)[0]:
            data, ancillary, flags, _ = self.sock.recvmsg(65536, socket.CMSG_SPACE(64 * 4))
            if not data:
                raise ConnectionError("Wayland compositor disconnected")
            for level, kind, payload in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    fds = array.array("i")
                    fds.frombytes(payload[:len(payload) - len(payload) % fds.itemsize])
                    self.fds.extend(fds)
            if flags & socket.MSG_CTRUNC:
                raise RuntimeError("Wayland ancillary data truncated")
            self.buffer.extend(data)
        while len(self.buffer) >= 8:
            obj, word = struct.unpack_from("=II", self.buffer)
            size, opcode = word >> 16, word & 0xffff
            if size < 8 or size % 4:
                raise RuntimeError("Invalid Wayland event framing")
            if len(self.buffer) < size:
                break
            payload = bytes(self.buffer[8:size])
            del self.buffer[:size]
            handler = self.handlers.get(obj)
            if handler:
                handler(opcode, payload)

    def until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Wayland response timed out")
            self.dispatch(min(remaining, .1))

    def roundtrip(self):
        done = []
        callback = self.new(lambda op, data: done.append(True))
        self.send(1, 0, uint(callback))
        self.until(lambda: done)

    def close(self):
        self.sock.close()
        while self.fds:
            os.close(self.fds.popleft())


class DataControl:
    """Clipboard ownership on this Wayland seat (isolated seats stay isolated)."""
    def __init__(self, connection):
        self.wl = connection
        self.manager = connection.bind("zwlr_data_control_manager_v1", 1)
        seat = connection.bind("wl_seat", 1)
        self.device = connection.new(self._device)
        connection.send(self.manager, 1, uint(self.device, seat))
        self.text = b""
        self.source = None
        self.transfers = 0

    def _device(self, opcode, data):
        # data_offer objects own no received FDs; destroy offers immediately.
        if opcode == 0:
            offer, = struct.unpack_from("=I", data)
            self.wl.handlers[offer] = lambda op, data: None
            self.wl.send(offer, 1)

    def _source(self, opcode, data):
        if opcode == 0:
            mime, _ = read_string(data)
            fd = self.wl.fds.popleft()
            try:
                # Nonblocking bounded writes so a non-reading clipboard client
                # cannot stall the controller. Large text is capped by the API.
                os.set_blocking(fd, False)
                view = memoryview(self.text)
                deadline = time.monotonic() + 2
                while view:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Clipboard consumer stopped reading")
                    if not select.select([], [fd], [], .1)[1]:
                        continue
                    view = view[os.write(fd, view):]
                self.transfers += 1
            except BrokenPipeError:
                pass
            finally:
                os.close(fd)

    def set(self, value):
        old = self.source
        self.text = value.encode("utf-8")
        self.source = self.wl.new(self._source)
        self.wl.send(self.manager, 0, uint(self.source))
        for mime in ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING"):
            self.wl.send(self.source, 0, string(mime))
        self.wl.send(self.device, 0, uint(self.source))
        if old:
            self.wl.send(old, 1)
        self.wl.roundtrip()
