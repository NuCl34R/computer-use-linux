#!/usr/bin/env python3
"""Portable entrypoint for any harness with shell access or MCP support."""
import argparse
import base64
import json
import os
import pathlib
import sys


def main():
    if os.environ.get("CUL_DEBUG_TRACE") == "1":
        import faulthandler
        import signal
        faulthandler.enable(all_threads=True)
        faulthandler.register(signal.SIGUSR1, all_threads=True)
        def trace_term(signum, frame):
            faulthandler.dump_traceback(all_threads=True)
            signal.signal(signum, signal.SIG_DFL)
            signal.raise_signal(signum)
        signal.signal(signal.SIGTERM, trace_term)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Read-only dependency and desktop report")
    commands.add_parser("mcp", help="MCP JSON-RPC over stdio (no daemon installation)")
    server = commands.add_parser("serve", help="Persistent same-user Unix-socket controller")
    server.add_argument("--socket", required=True)
    server.add_argument("--mode", choices=["current", "isolated"], default="isolated")
    server.add_argument("--backend", choices=["auto", "portal", "wlr", "x11"], default="auto")
    server.add_argument("--compositor", choices=["auto", "kwin", "hyprland", "sway", "xvfb"], default="auto")
    server.add_argument("--width", type=int, default=1280)
    server.add_argument("--height", type=int, default=800)
    server.add_argument("--persist", action="store_true", help="Request a portal restore token in current mode")
    client = commands.add_parser("call", help="Call a tool on a running controller")
    client.add_argument("tool")
    client.add_argument("--socket", required=True)
    client.add_argument("--json", default="{}", help="Tool argument object")
    client.add_argument("--json-file", help="Read tool arguments from a file, avoiding shell quoting")
    client.add_argument("--image-out", help="Save returned screenshot to this path; omit base64 from JSON output")
    commands.add_parser("tools", help="Print the shared MCP/CLI tool schemas")
    args = parser.parse_args()
    try:
        if args.command == "call":
            from cul.server import call
            params = json.loads(pathlib.Path(args.json_file).read_text() if args.json_file else args.json)
            result = call(args.socket, args.tool, params)
            picture = result.get("result", {}).get("image")
            if picture and args.image_out:
                path = pathlib.Path(args.image_out).absolute()
                data = base64.b64decode(picture.pop("data"), validate=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                picture["path"] = str(path)
            elif picture:
                picture.pop("data", None)
                picture["note"] = "Use --image-out to save the image, or MCP to receive it inline"
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 0 if result["ok"] and not result.get("result", {}).get("error") else 1
        if args.command == "doctor":
            from cul.engine import doctor
            print(json.dumps(doctor(), ensure_ascii=False, indent=2))
        elif args.command == "tools":
            from cul.engine import TOOLS
            print(json.dumps({name: {"description": d, "inputSchema": s} for name, (d, s, _) in TOOLS.items()}, indent=2))
        elif args.command == "mcp":
            from cul.server import mcp
            mcp()
        elif args.command == "serve":
            from cul.server import serve
            serve(args.socket, {name: getattr(args, name) for name in ("mode", "backend", "compositor", "width", "height", "persist")})
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
