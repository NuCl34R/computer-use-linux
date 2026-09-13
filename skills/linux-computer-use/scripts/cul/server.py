"""MCP stdio and a same-user Unix-socket transport; no open TCP listener."""
import json
import math
import os
import pathlib
import queue
import signal
import socket
import socketserver
import stat
import struct
import sys
import threading

from .engine import Engine, TOOLS
from . import __version__

MAX_MESSAGE = 2 * 1024 * 1024


def validate(value, schema, path="arguments"):
    if type(value) is float and not math.isfinite(value):
        raise ValueError(f"{path}: numbers must be finite")
    types = schema.get("type")
    if types:
        types = [types] if isinstance(types, str) else types
        matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
            "string": isinstance(value, str), "integer": type(value) is int,
            "number": type(value) in (int, float), "boolean": type(value) is bool, "null": value is None}
        if not any(matches.get(t, False) for t in types):
            raise ValueError(f"{path}: expected {types}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: expected one of {schema['enum']}")
    if isinstance(value, dict) and "properties" in schema:
        props = schema["properties"]
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ValueError(f"{path}: unknown fields {sorted(set(value) - set(props))}")
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise ValueError(f"{path}: missing {sorted(missing)}")
        for key, item in value.items():
            if key in props:
                validate(item, props[key], path + "." + key)
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{index}]")


def execute(engine, name, arguments):
    if name not in TOOLS:
        raise ValueError(f"Unknown tool {name!r}")
    validate(arguments, TOOLS[name][1])
    return engine.dispatch(name, arguments)


def content(result):
    result = dict(result)
    picture = result.get("image")
    blocks = []
    if picture:
        picture = dict(picture)
        blocks.append({"type": "image", "data": picture.pop("data"), "mimeType": picture["mimeType"]})
        result["image"] = picture
    blocks.insert(0, {"type": "text", "text": json.dumps(result, ensure_ascii=False, allow_nan=False)})
    return {"content": blocks, "structuredContent": result, "isError": bool(result.get("error"))}


def mcp():
    engine = Engine()
    messages = queue.Queue(maxsize=64)
    active = {"id": None}
    terminating = threading.Event()
    def terminate(signum, frame):
        engine.cancel.set()
        terminating.set()
        try:
            messages.put_nowait(None)
        except queue.Full:
            pass
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    def reader():
        buffered = bytearray()
        while True:
            # Raw reads avoid a daemon thread holding stdin's BufferedReader
            # lock during interpreter shutdown after SIGTERM.
            while b"\n" not in buffered and len(buffered) <= MAX_MESSAGE:
                chunk = os.read(sys.stdin.fileno(), 65536)
                if not chunk:
                    break
                buffered.extend(chunk)
            if b"\n" in buffered:
                end = buffered.index(b"\n") + 1
                line = bytes(buffered[:end])
                del buffered[:end]
            else:
                line = bytes(buffered)
                buffered.clear()
            if not line:
                engine.cancel.set()
                messages.put(None)
                return
            if len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
                messages.put({"parse_error": "Message exceeds 2 MiB or lacks newline"})
                engine.cancel.set()
                messages.put(None)
                return
            try:
                obj = json.loads(line, parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
            except (ValueError, UnicodeDecodeError):
                messages.put({"parse_error": "Invalid JSON"})
                continue
            if isinstance(obj, dict) and obj.get("method") == "notifications/cancelled":
                if obj.get("params", {}).get("requestId") == active["id"]:
                    engine.cancel.set()
                continue
            messages.put(obj)

    threading.Thread(target=reader, daemon=True, name="cul-mcp-reader").start()
    initialized = False
    try:
        while True:
            request = messages.get()
            if request is None or terminating.is_set():
                break
            request_id = request.get("id") if isinstance(request, dict) else None
            response = {"jsonrpc": "2.0", "id": request_id}
            try:
                if not isinstance(request, dict) or "parse_error" in request or request.get("jsonrpc") != "2.0":
                    raise ValueError("Invalid JSON-RPC request")
                method, params = request.get("method"), request.get("params", {})
                if not isinstance(params, dict):
                    raise ValueError("params must be an object")
                if "id" not in request:
                    continue
                active["id"] = request_id
                if method == "initialize":
                    version = params.get("protocolVersion")
                    supported = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
                    response["result"] = {"protocolVersion": version if version in supported else "2025-06-18",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "linux-computer-use", "version": __version__},
                        "instructions": "Start a session, observe get_state, act on returned tokens or screenshot pixels with frame_id, and verify. Isolated mode leaves the user's desktop usable."}
                    initialized = True
                elif method == "ping":
                    response["result"] = {}
                elif not initialized:
                    raise ValueError("Initialize MCP before calling tools")
                elif method == "tools/list":
                    response["result"] = {"tools": [{"name": name, "description": desc, "inputSchema": schema,
                        "annotations": {"readOnlyHint": readonly, "destructiveHint": not readonly,
                                        "idempotentHint": readonly, "openWorldHint": True}}
                        for name, (desc, schema, readonly) in TOOLS.items()]}
                elif method == "tools/call":
                    try:
                        response["result"] = content(execute(engine, params.get("name"), params.get("arguments", {})))
                    except Exception as error:
                        response["result"] = {"content": [{"type": "text", "text": str(error)}], "isError": True}
                else:
                    response["error"] = {"code": -32601, "message": "Unknown method"}
            except Exception as error:
                response["error"] = {"code": -32600, "message": str(error)}
            finally:
                active["id"] = None
            print(json.dumps(response, ensure_ascii=False, allow_nan=False), flush=True)
    finally:
        engine.close()


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False


def serve(path, start):
    path = pathlib.Path(path).absolute()
    if len(os.fsencode(path)) >= 104:
        raise ValueError("Unix socket path too long; choose a shorter private directory")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = path.parent.stat()
    if directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) & 0o077:
        raise PermissionError("Socket directory must be owned by this user and mode 0700")
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Socket path already exists: {path}; do not overwrite an active session")
    engine = Engine()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(150)
            _, uid, _ = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid():
                return
            line = self.rfile.readline(MAX_MESSAGE + 1)
            if len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
                return
            request = {}
            try:
                request = json.loads(line, parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
                if not isinstance(request, dict) or set(request) - {"tool", "arguments"}:
                    raise ValueError("Expected {tool, arguments}")
                result = execute(engine, request.get("tool"), request.get("arguments", {}))
                response = {"ok": True, "result": result}
            except Exception as error:
                response = {"ok": False, "error": str(error)}
            self.wfile.write(json.dumps(response, ensure_ascii=False, allow_nan=False).encode() + b"\n")
            if request.get("tool") == "stop":
                threading.Thread(target=self.server.shutdown, daemon=True).start()

    server = UnixServer(str(path), Handler)
    os.chmod(path, 0o600)

    def terminate(signum, frame):
        engine.cancel.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        info = engine.dispatch("start_session", start)
        ready = path.with_suffix(".ready.json")
        ready.write_text(json.dumps(info))
        ready.chmod(0o600)
        print(json.dumps({"ready": True, "socket": str(path), **info}), flush=True)
        server.serve_forever(poll_interval=.1)
    finally:
        engine.close()
        server.server_close()
        path.unlink(missing_ok=True)
        path.with_suffix(".ready.json").unlink(missing_ok=True)


def call(path, tool, arguments):
    request = json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False, allow_nan=False).encode() + b"\n"
    if len(request) > MAX_MESSAGE:
        raise ValueError("Request exceeds 2 MiB")
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(150)
        sock.connect(path)
        sock.sendall(request)
        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError("Controller disconnected before replying")
            response.extend(chunk)
            if len(response) > 12 * 1024 * 1024:
                raise ValueError("Response exceeds 12 MiB")
        return json.loads(response)
