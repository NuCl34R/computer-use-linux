"""On-demand spectator transport and local browser viewer, separate from agent input."""
import hmac
import http.server
import json
import os
import pathlib
import queue
import re
import secrets
import select
import signal
import subprocess
import socket
import socketserver
import stat
import struct
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from concurrent.futures import Future

LIMIT = 6 * 1024 * 1024
SESSION = re.compile(r"[0-9a-f]{16}")


def private_directory(path):
    path = pathlib.Path(path).absolute()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise PermissionError("Viewer directory must be owned by this user and mode 0700")
    return path


def registry_directory():
    explicit = os.environ.get("CUL_WATCH_DIR")
    if explicit:
        return private_directory(explicit)
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        info = pathlib.Path(runtime).lstat()
        if stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077:
            return private_directory(pathlib.Path(runtime) / "linux-computer-use/watch")
    return private_directory(pathlib.Path(tempfile.gettempdir()) / ("cul-watch-" + str(os.getuid())))


def container_directory():
    # Only this container's spectator sockets are shared with the host.
    return private_directory(registry_directory() / ("c-" + secrets.token_hex(8)))


def peer_is_owner(sock):
    _, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    return uid == os.getuid()


def request(path, operation, **arguments):
    path = pathlib.Path(path)
    directory = path.parent.lstat()
    info = path.lstat()
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) & 0o077:
        raise PermissionError("Untrusted spectator directory")
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise PermissionError("Untrusted spectator socket")
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(.4 if operation == "status" else 7)
        sock.connect(str(path))
        if not peer_is_owner(sock):
            raise PermissionError("Spectator server belongs to another user")
        sock.sendall(json.dumps({"operation": operation, **arguments}).encode() + b"\n")
        response = bytearray()
        while not response.endswith(b"\n"):
            part = sock.recv(65536)
            if not part:
                raise ConnectionError("Spectator disconnected")
            response.extend(part)
            if len(response) > LIMIT:
                raise ValueError("Spectator response exceeds limit")
    return json.loads(response)


def sockets(directory):
    candidates = list(directory.glob("*.sock"))
    for child in directory.glob("c-*"):
        if re.fullmatch(r"c-[0-9a-f]{16}", child.name) and not child.is_symlink():
            candidates.extend(child.glob("*.sock"))
    return [p for p in sorted(candidates) if SESSION.fullmatch(p.stem)]


def capture_worker(kind, display, transform, expected_parent):
    # A blocked Xlib call or fatal X I/O error must not take down the controller.
    # Linux kills this owned helper if its controller disappears unexpectedly.
    import ctypes
    ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0)
    if os.getppid() != expected_parent:
        return
    if kind == "x11":
        from .x11 import X11
        source = X11()
        close = source.close
    else:
        from .wayland import Wayland
        from .wlr import WlrCapture
        wl = Wayland()
        source = WlrCapture(wl, wl.bind("wl_output", 1, index=display), transform, cursor=True)
        close = wl.close
    try:
        for line in sys.stdin:
            if line != "frame\n":
                break
            picture = source.snapshot(1600, 1000, "jpeg", 75, None)
            print(json.dumps(picture), flush=True)
    finally:
        close()


class WorkerCapture:
    def __init__(self, kind, display, transform):
        code = "import sys; sys.path.insert(0, sys.argv[1]); from cul.watch import capture_worker; capture_worker(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))"
        self.process = subprocess.Popen([sys.executable, "-c", code, str(pathlib.Path(__file__).parent.parent), kind, str(display), str(transform), str(os.getpid())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)

    def snapshot(self):
        self.process.stdin.write(b"frame\n")
        result = bytearray()
        deadline = time.monotonic() + 5
        while not result.endswith(b"\n"):
            if not select.select([self.process.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
                self.interrupt()
                raise TimeoutError("Preview capture timed out")
            part = os.read(self.process.stdout.fileno(), 65536)
            if not part:
                raise ConnectionError("Preview capture ended")
            result.extend(part)
            if len(result) > LIMIT:
                self.interrupt()
                raise ValueError("Preview exceeds limit")
        return json.loads(result)

    def interrupt(self):
        if self.process.poll() is None:
            self.process.kill()

    def close(self):
        self.interrupt()
        self.process.wait(timeout=2)
        self.process.stdin.close()
        self.process.stdout.close()


class Capture:
    """Independent display process, or the existing immutable PipeWire sample."""
    def __init__(self, engine, display):
        backend = engine.backend
        if backend.name in ("wlr", "x11"):
            self.worker = WorkerCapture(backend.name, display, backend.displays[display].get("transform", 0))
            self.source = None
        else:
            # PipeWireCapture.snapshot copies an immutable sample under its own
            # condition. It neither dispatches input nor creates agent frame IDs.
            self.worker = None
            self.source = backend.captures[display]

    def snapshot(self):
        return self.worker.snapshot() if self.worker else self.source.snapshot(1600, 1000, "jpeg", 75, None)

    def interrupt(self):
        if self.worker:
            self.worker.interrupt()

    def close(self):
        if self.worker:
            self.worker.close()


class SpectatorServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class Spectator:
    def __init__(self, engine, stop):
        self.engine, self.stop = engine, stop
        self.directory = registry_directory()
        self.path = self.directory / (engine.session_id + ".sock")
        if len(os.fsencode(self.path)) >= 104:
            raise ValueError("Spectator socket path too long; choose a shorter CUL_WATCH_DIR")
        if self.path.exists() or self.path.is_symlink():
            raise FileExistsError("Spectator socket already exists")
        self.capture_lock = threading.Lock()
        self.captures, self.cache = {}, {}
        self.closed = False
        self.transport = os.environ.get("CUL_WATCH_TRANSPORT", "native")
        owner = self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.request.settimeout(8)
                if not peer_is_owner(self.request):
                    return
                line = self.rfile.readline(4097)
                if len(line) > 4096 or not line.endswith(b"\n"):
                    return
                try:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        raise ValueError("Expected an object")
                    operation = obj.get("operation")
                    if operation == "status" and set(obj) == {"operation"}:
                        response = owner.status()
                    elif operation == "frame" and set(obj) <= {"operation", "display"}:
                        response = owner.frame(obj.get("display", 0))
                    elif operation == "stop" and set(obj) == {"operation"}:
                        owner.engine.cancel.set()
                        owner.stop()
                        response = {"stopping": True}
                    else:
                        raise ValueError("Spectator supports status, frame and stop only")
                except Exception as error:
                    response = {"error": str(error)}
                try:
                    self.wfile.write(json.dumps(response, allow_nan=False).encode() + b"\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass
        self.server = SpectatorServer(str(self.path), Handler)
        self.path.chmod(0o600)
        self.requests = queue.Queue(maxsize=1)
        # Linux PDEATHSIG is tied to the creating thread. Keep helper creation
        # on this persistent thread, never on a short-lived socket handler.
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True, name="cul-preview-capture")
        self.capture_thread.start()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .1}, daemon=True, name="cul-spectator")
        self.thread.start()

    def status(self):
        engine = self.engine
        backend = engine.backend
        state = "stopped" if engine.cleanup_complete else "stopping" if engine.cancel.is_set() else "running" if backend else "waiting"
        return {"session_id": engine.session_id, "state": state, "mode": engine.mode,
            "backend": backend.name if backend else None, "activity": engine.activity,
            "displays": backend.displays if backend else [], "transport": self.transport}

    def _capture_loop(self):
        while True:
            job = self.requests.get()
            if job is None:
                return
            display, future = job
            try:
                future.set_result(self._frame(display))
            except Exception as error:
                future.set_exception(error)

    def frame(self, display):
        if self.closed:
            return self.status()
        future = Future()
        try:
            self.requests.put_nowait((display, future))
        except queue.Full:
            return {**self.status(), "busy": True}
        return future.result(timeout=7)

    def _frame(self, display):
        if type(display) is not int or not 0 <= display < 32:
            raise ValueError("Invalid display")
        status = self.status()
        if status["state"] != "running" or self.closed:
            return status
        if display >= len(status["displays"]):
            raise ValueError("Invalid display")
        if not self.capture_lock.acquire(blocking=False):
            return {**status, "busy": True}
        try:
            if self.closed:
                return {**status, "state": "stopped"}
            now = time.monotonic()
            cached = self.cache.get(display)
            if cached and now - cached[0] < .1:
                return {**status, "image": cached[1]}
            if display not in self.captures:
                # Creation briefly coordinates with startup/teardown. Subsequent
                # frames never take the agent's action lock, including during drag.
                if not self.engine.lock.acquire(blocking=False):
                    return {**status, "busy": True}
                try:
                    if not self.engine.backend or self.engine.ended:
                        return self.status()
                    self.captures[display] = Capture(self.engine, display)
                finally:
                    self.engine.lock.release()
            try:
                picture = self.captures[display].snapshot()
            except Exception:
                failed = self.captures.pop(display)
                failed.close()
                self.cache.pop(display, None)
                raise
            self.cache[display] = (time.monotonic(), picture)
            return {**self.status(), "image": picture}
        finally:
            self.capture_lock.release()

    def close_captures(self):
        self.closed = True
        deadline = time.monotonic() + 6
        while not self.capture_lock.acquire(timeout=.05):
            for capture in tuple(self.captures.values()):
                capture.interrupt()
            if time.monotonic() >= deadline:
                return
        try:
            for capture in self.captures.values():
                try:
                    capture.close()
                except Exception:
                    pass
            self.captures.clear()
            self.cache.clear()
        finally:
            self.capture_lock.release()

    def close(self):
        self.close_captures()
        self.server.shutdown()
        self.server.server_close()
        self.path.unlink(missing_ok=True)
        self.requests.put(None, timeout=1)
        self.capture_thread.join(timeout=2)


def enable(engine, stop):
    if os.environ.get("CUL_WATCH", "1") == "0":
        return None
    try:
        return Spectator(engine, stop)
    except (OSError, ValueError) as error:
        print("Spectator unavailable: " + str(error), file=sys.stderr)
        return None


class Viewer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, directory, port=0):
        self.directory = directory
        self.token = secrets.token_urlsafe(32)
        self.page = pathlib.Path(__file__).with_name("watch.html").read_bytes()
        super().__init__(("127.0.0.1", port), ViewerHandler)
        self.origin = "http://127.0.0.1:" + str(self.server_port)
        self.url = self.origin + "/#token=" + self.token

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address

    def session_path(self, session):
        if not isinstance(session, str) or not SESSION.fullmatch(session):
            raise ValueError("Invalid session")
        found = [p for p in sockets(self.directory) if p.stem == session]
        if len(found) != 1:
            raise ValueError("Session is no longer available")
        return found[0]


class ViewerHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, data, mime="application/json"):
        body = json.dumps(data).encode() if mime == "application/json" else data
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self, authenticated=True):
        if self.headers.get("Host") != urllib.parse.urlsplit(self.server.origin).netloc:
            self.reply(403, {"error": "Invalid host"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            self.reply(403, {"error": "Invalid origin"})
            return False
        if authenticated and not hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + self.server.token).encode()):
            self.reply(403, {"error": "Viewer authorization required"})
            return False
        return True

    def do_GET(self):
        self.connection.settimeout(10)
        url = urllib.parse.urlsplit(self.path)
        if not self.allowed(authenticated=url.path != "/"):
            return
        if url.path == "/":
            self.reply(200, self.server.page, "text/html; charset=utf-8")
            return
        try:
            if url.path == "/api/sessions":
                sessions = []
                for path in sockets(self.server.directory):
                    try:
                        info = request(path, "status")
                        if info.get("session_id") == path.stem:
                            sessions.append(info)
                            if len(sessions) >= 64:
                                break
                    except (OSError, ValueError, ConnectionError):
                        continue
                self.reply(200, {"sessions": sessions})
            elif url.path == "/api/frame":
                query = urllib.parse.parse_qs(url.query)
                path = self.server.session_path(query.get("session", [None])[0])
                display = int(query.get("display", ["0"])[0])
                self.reply(200, request(path, "frame", display=display))
            else:
                self.reply(404, {"error": "Not found"})
        except (OSError, ValueError, ConnectionError) as error:
            self.reply(400, {"error": str(error)})

    def do_POST(self):
        self.connection.settimeout(10)
        if not self.allowed():
            return
        if self.path != "/api/stop":
            self.reply(404, {"error": "Not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Expected a small JSON request")
            obj = json.loads(self.rfile.read(size))
            if not isinstance(obj, dict) or set(obj) != {"session"}:
                raise ValueError("Expected a session identifier")
            path = self.server.session_path(obj["session"])
            self.reply(200, request(path, "stop"))
        except (OSError, ValueError, ConnectionError) as error:
            self.reply(400, {"error": str(error)})


def run(args):
    directory = registry_directory()
    if args.list:
        sessions = []
        for path in sockets(directory):
            try:
                sessions.append(request(path, "status"))
            except (OSError, ValueError, ConnectionError):
                pass
        print(json.dumps({"sessions": sessions}, indent=2))
        return
    server = Viewer(directory, args.port)
    url = server.url
    if args.session:
        if not SESSION.fullmatch(args.session):
            raise ValueError("Invalid session identifier")
        url = server.origin + "/?session=" + args.session + "#token=" + server.token
    print(json.dumps({"viewer_url": url, "note": "Close the browser tab to stop watching. Ctrl+C closes this viewer; agent sessions keep running."}), flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
