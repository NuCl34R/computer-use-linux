"""Serialized observe/act/verify engine shared by MCP and the Unix-socket CLI."""
import collections
import math
import os
import pathlib
import platform
import secrets
import shutil
import subprocess
import threading
import time

BUTTONS = {"left": 272, "right": 273, "middle": 274, "back": 275, "forward": 276}


def number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a finite number in {low}..{high}")
    return value


def doctor():
    import importlib.util
    required = ["python3", "dbus-daemon"]
    commands = {name: shutil.which(name) for name in required + ["kwin_wayland", "pipewire", "wireplumber", "Hyprland", "sway", "Xvfb", "podman"]}
    packages = {name: importlib.util.find_spec(name) is not None for name in ("gi", "dbus")}
    result = {"platform": platform.platform(), "desktop": os.environ.get("XDG_CURRENT_DESKTOP"),
        "session_type": os.environ.get("XDG_SESSION_TYPE"), "commands": commands, "python_modules": packages,
        "root_required": False, "isolated_compositors": [name for name in ("kwin_wayland", "Hyprland", "sway", "Xvfb") if commands[name]]}
    try:
        from .dbusutil import session_bus, plain, close_bus
        import dbus
        bus = session_bus()
        try:
            props = dbus.Interface(bus.get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"),
                                   "org.freedesktop.DBus.Properties")
            result["remote_desktop_portal"] = plain(props.GetAll("org.freedesktop.portal.RemoteDesktop", timeout=3))
        finally:
            close_bus(bus)
    except Exception as error:
        result["remote_desktop_portal"] = {"error": str(error)}
    result["ready_to_try"] = all(packages.values()) and bool(result["isolated_compositors"] or result["remote_desktop_portal"].get("AvailableDeviceTypes"))
    result["note"] = "Discovery only. Readiness is proved by start_session and real E2E tests, not by installed binaries."
    return result


class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.backend = None
        self.desktop = None
        self.a11y = None
        self.windowing = None
        self.ended = False
        self.frames = collections.OrderedDict()
        self.session_id = secrets.token_hex(8)
        self.last_action = None
        self.mode = None
        self.apps = []

    def dispatch(self, method, params=None):
        params = params or {}
        if not isinstance(params, dict):
            raise ValueError("Tool arguments must be an object")
        if method == "stop":
            self.cancel.set()
        with self.lock:
            started = time.monotonic()
            if method not in TOOLS:
                raise ValueError(f"Unknown tool {method!r}")
            schema = TOOLS[method][1]
            unknown = set(params) - set(schema["properties"])
            missing = set(schema.get("required", [])) - set(params)
            if unknown or missing:
                raise ValueError(f"Invalid arguments; unknown={sorted(unknown)}, missing={sorted(missing)}")
            if method == "doctor":
                result = doctor()
            elif method == "start_session":
                result = self.start(**params)
            elif method == "stop":
                self.close()
                result = {"stopped": True}
            else:
                if not self.backend:
                    raise RuntimeError("No active session; call start_session first")
                self.backend.check()
                if self.cancel.is_set():
                    raise RuntimeError("Session stopped")
                result = getattr(self, method)(**params)
            result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            return result

    def start(self, mode="isolated", backend="auto", compositor="auto", width=1280, height=800, persist=False):
        if self.backend:
            return self.info()
        if self.ended:
            raise RuntimeError("This session ended; restart the server to create a fresh desktop environment")
        if mode not in ("isolated", "current") or backend not in ("auto", "portal", "wlr", "x11"):
            raise ValueError("Invalid mode or backend")
        number(width, "width", 640, 4096)
        number(height, "height", 480, 4096)
        if type(width) is not int or type(height) is not int:
            raise ValueError("Desktop width and height must be integers")
        self.mode = mode
        try:
            if mode == "isolated":
                from .isolated import IsolatedDesktop
                if compositor == "auto":
                    compositor = os.environ.get("CUL_DEFAULT_COMPOSITOR", "auto")
                self.desktop = IsolatedDesktop(width, height, compositor)
                self.desktop.activate_environment()
                backend = {"kwin": "kwin-private", "xvfb": "x11"}.get(self.desktop.compositor, "wlr")
            elif backend == "auto":
                if os.environ.get("XDG_SESSION_TYPE") == "x11":
                    backend = "x11"
                elif any(name in os.environ.get("XDG_CURRENT_DESKTOP", "").lower() for name in ("hyprland", "sway")):
                    backend = "wlr"
                else:
                    backend = "portal"
            if backend == "x11" and os.environ.get("XDG_SESSION_TYPE") != "x11":
                raise RuntimeError("XTEST cannot control native Wayland windows; select portal or wlr")
            if backend == "kwin-private":
                from .kwin import KWinPrivate
                self.backend = KWinPrivate(width, height)
            elif backend == "portal":
                from .portal import Portal
                token = pathlib.Path(os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local/state")) / "linux-computer-use/portal-token" if persist else None
                self.backend = Portal(token)
            elif backend == "wlr":
                from .wlr import Wlr
                self.backend = Wlr()
            else:
                from .x11 import X11
                self.backend = X11()
            from .windows import Windows
            self.windowing = Windows()
            return self.info()
        except BaseException:
            self.close()
            raise

    def info(self):
        return {"session_id": self.session_id, "mode": self.mode, "backend": self.backend.name,
            "displays": self.backend.displays, "private_directory": str(self.desktop.root) if self.desktop else None,
            "concurrent_user": self.mode == "isolated",
            "clipboard_scope": "private desktop" if self.mode == "isolated" else "user desktop (typing may replace clipboard)",
            "coordinate_contract": "Coordinates default to logical pixels relative to display. With frame_id, use pixels in that returned screenshot.",
            "isolation": "Separate display, input seat, clipboard and session bus; apps retain the user's file/network permissions."
                if self.mode == "isolated" else "Uses the selected existing desktop and shares its input/focus."}

    def _accessibility(self):
        if self.a11y is None:
            from .accessibility import Accessibility
            self.a11y = Accessibility()
        return self.a11y

    def list_apps(self):
        return {"apps": self._accessibility().apps()}

    def list_windows(self):
        return {"windows": self.windowing.list()}

    def focus_window(self, window_id):
        result = self.windowing.focus(str(window_id))
        self.last_action = time.monotonic()
        return result

    def launch_app(self, argv):
        if not isinstance(argv, list) or not argv or len(argv) > 100 or any(not isinstance(a, str) or "\0" in a for a in argv):
            raise ValueError("argv must be a nonempty argument vector, without a shell")
        if self.desktop:
            pid = self.desktop.launch_app(argv)
        else:
            app = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
            self.apps.append(app)
            pid = app.pid
        return {"pid": pid, "mode": self.mode}

    def screenshot(self, display=0, max_width=1600, max_height=1000, format="jpeg", quality=85, settle_ms=40):
        if type(display) is not int or not 0 <= display < len(self.backend.displays):
            raise ValueError("Invalid display id")
        number(settle_ms, "settle_ms", 0, 2000)
        after = self.last_action
        settle_until = after + settle_ms / 1000 if after is not None else None
        if settle_until and time.monotonic() < settle_until:
            if self.cancel.wait(settle_until - time.monotonic()):
                raise RuntimeError("Capture cancelled")
        result = self.backend.captures[display].snapshot(max_width, max_height, format, quality, after)
        d = self.backend.displays[display]
        frame_id = secrets.token_hex(8)
        self.frames[frame_id] = {"display": display, "image_width": result["width"], "image_height": result["height"],
                                 "width": d["width"], "height": d["height"], "created": time.monotonic()}
        while len(self.frames) > 16:
            self.frames.popitem(last=False)
        result.update(frame_id=frame_id, display=display, logical_width=d["width"], logical_height=d["height"],
                      scale_x=d["width"] / result["width"], scale_y=d["height"] / result["height"])
        return {"image": result}

    def get_state(self, pid=None, screenshot=True, display=0, max_nodes=350):
        result = {}
        try:
            result["accessibility"] = self._accessibility().snapshot(pid=pid, max_nodes=max_nodes)
        except Exception as error:
            result["accessibility_error"] = str(error)
        try:
            result["windows"] = self.windowing.list()
        except Exception as error:
            result["window_error"] = str(error)
        if screenshot:
            result.update(self.screenshot(display=display))
        return result

    def _coords(self, x, y, display=0, frame_id=None):
        number(x, "x", 0, 65535)
        number(y, "y", 0, 65535)
        if frame_id:
            frame = self.frames.get(frame_id)
            if not frame or time.monotonic() - frame["created"] > 60:
                raise ValueError("Unknown or expired frame_id; take a fresh screenshot")
            if x >= frame["image_width"] or y >= frame["image_height"]:
                raise ValueError("Coordinates lie outside the referenced screenshot")
            display = frame["display"]
            x, y = x * frame["width"] / frame["image_width"], y * frame["height"] / frame["image_height"]
        if type(display) is not int or not 0 <= display < len(self.backend.displays):
            raise ValueError("Invalid display id")
        d = self.backend.displays[display]
        if x >= d["width"] or y >= d["height"]:
            raise ValueError("Coordinates lie outside the display")
        return x, y, display

    def move(self, x, y, display=0, frame_id=None):
        x, y, display = self._coords(x, y, display, frame_id)
        self.backend.move(x, y, display)
        self.backend.sync()
        self.last_action = time.monotonic()
        return {"x": x, "y": y, "display": display}

    def move_relative(self, dx, dy):
        number(dx, "dx", -10000, 10000)
        number(dy, "dy", -10000, 10000)
        self.backend.move_relative(dx, dy)
        self.backend.sync()
        self.last_action = time.monotonic()
        return {"dx": dx, "dy": dy, "relative": True}

    def click(self, x=None, y=None, display=0, frame_id=None, button="left", count=1, token=None):
        if token:
            if x is not None or y is not None:
                raise ValueError("Choose an element token or coordinates")
            return self.perform_action(token)
        if button not in BUTTONS or type(count) is not int or not 1 <= count <= 3:
            raise ValueError("Invalid button or click count")
        self.move(x, y, display, frame_id)
        code = BUTTONS[button]
        try:
            for i in range(count):
                if self.cancel.is_set():
                    raise RuntimeError("Action cancelled")
                self.backend.button(code, True)
                self.backend.sync()
                time.sleep(.012)
                self.backend.button(code, False)
                self.backend.sync()
                if i + 1 < count:
                    time.sleep(.05)
        finally:
            self.backend.button(code, False)
        self.last_action = time.monotonic()
        return {"clicked": True, "count": count}

    def press_key(self, key):
        self.backend.press(key)
        self.last_action = time.monotonic()
        return {"pressed": key}

    def type_text(self, text, paste_key="Ctrl+v"):
        if not isinstance(text, str) or len(text.encode("utf-8")) > 256 * 1024 or "\0" in text:
            raise ValueError("Text must be UTF-8 without NUL, at most 256 KiB")
        self.backend.type_text(text, paste_key)
        self.last_action = time.monotonic()
        return {"characters": len(text)}

    def scroll(self, dy, dx=0, x=None, y=None, display=0, frame_id=None):
        number(dx, "dx", -1500, 1500)
        number(dy, "dy", -1500, 1500)
        if x is not None or y is not None:
            self.move(x, y, display, frame_id)
        self.backend.scroll(dx, dy)
        self.last_action = time.monotonic()
        return {"scrolled": True}

    def drag(self, start_x, start_y, end_x, end_y, display=0, frame_id=None, duration_ms=350, button="left"):
        number(duration_ms, "duration_ms", 50, 10000)
        if button not in BUTTONS:
            raise ValueError("Invalid pointer button")
        sx, sy, display = self._coords(start_x, start_y, display, frame_id)
        ex, ey, end_display = self._coords(end_x, end_y, display, frame_id)
        if end_display != display:
            raise ValueError("Drag endpoints must be on the same display")
        steps = max(3, round(duration_ms / 16))
        code = BUTTONS[button]
        self.backend.move(sx, sy, display)
        try:
            self.backend.button(code, True)
            self.backend.sync()
            for i in range(1, steps + 1):
                if self.cancel.wait(duration_ms / steps / 1000):
                    raise RuntimeError("Drag cancelled")
                t = i / steps
                self.backend.move(sx + (ex - sx) * t, sy + (ey - sy) * t, display)
                self.backend.sync()
        finally:
            self.backend.button(code, False)
            self.backend.sync()
        self.last_action = time.monotonic()
        return {"dragged": True}

    def perform_action(self, token, action=None):
        result = self._accessibility().perform_action(token, action)
        self.last_action = time.monotonic()
        return result

    def set_value(self, token, value):
        if not isinstance(value, str):
            number(value, "value", -1e308, 1e308)
        if isinstance(value, str) and len(value.encode()) > 256 * 1024:
            raise ValueError("Value exceeds 256 KiB")
        result = self._accessibility().set_value(token, value)
        self.last_action = time.monotonic()
        return result

    def focus_element(self, token):
        result = self._accessibility().focus(token)
        self.last_action = time.monotonic()
        return result

    def batch(self, actions, screenshot=True, display=0):
        if not isinstance(actions, list) or not 1 <= len(actions) <= 64:
            raise ValueError("A batch contains 1..64 actions")
        allowed = {"click", "move", "move_relative", "press_key", "type_text", "scroll", "drag", "perform_action", "set_value", "focus_element", "focus_window"}
        # Validate shape for the whole batch before causing any effects.
        for action in actions:
            if not isinstance(action, dict) or set(action) - {"tool", "arguments"} or action.get("tool") not in allowed or not isinstance(action.get("arguments", {}), dict):
                raise ValueError("Invalid batch action")
        results = []
        for index, action in enumerate(actions):
            try:
                if self.cancel.is_set():
                    raise RuntimeError("Batch cancelled")
                results.append(self.dispatch(action["tool"], action.get("arguments", {})))
            except Exception as error:
                return {"results": results, "failed_index": index, "error": str(error), "completed": False}
        result = {"results": results, "completed": True}
        if screenshot:
            result.update(self.screenshot(display=display))
        return result

    def close(self):
        self.cancel.set()
        self.ended = True
        errors = []
        had_native_session = bool(self.backend or self.windowing or self.a11y)
        if self.backend:
            try:
                self.backend.close()
            except Exception as error:
                errors.append(str(error))
            finally:
                self.backend = None
        if self.windowing:
            try:
                self.windowing.close()
            except Exception as error:
                errors.append(str(error))
            finally:
                self.windowing = None
        if had_native_session:
            from .dbusutil import shutdown
            try:
                shutdown(self.a11y.close if self.a11y else None)
            except Exception as error:
                errors.append(str(error))
            self.a11y = None
        if self.desktop:
            try:
                self.desktop.close()
            finally:
                self.desktop = None
        if errors:
            import sys
            print("Session cleanup: " + "; ".join(errors), file=sys.stderr)


def schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}

S = {"type": "string"}
N = {"type": "number"}
I = {"type": "integer"}
B = {"type": "boolean"}
COORDS = {"x": N, "y": N, "display": I, "frame_id": S}
TOOLS = {
    "doctor": ("Discover desktop interfaces and missing dependencies without opening a control session.", schema(), True),
    "start_session": ("Start persistent computer use. Isolated creates a private desktop so the user can keep working; current controls the user's session and may show portal consent.", schema({"mode": {"enum": ["isolated", "current"]}, "backend": {"enum": ["auto", "portal", "wlr", "x11"]}, "compositor": {"enum": ["auto", "kwin", "hyprland", "sway", "xvfb"]}, "width": I, "height": I, "persist": B}), False),
    "list_apps": ("List applications exposed on this session's AT-SPI bus.", schema(), True),
    "list_windows": ("List native compositor windows, including native Wayland apps.", schema(), True),
    "get_state": ("Observe bounded accessibility tree, stable element tokens, windows and a screenshot together.", schema({"pid": I, "screenshot": B, "display": I, "max_nodes": I}), True),
    "screenshot": ("Capture a bounded image; frame_id maps image pixels to logical coordinates. After input, allow 40 ms for repaint by default; settle_ms can override it.", schema({"display": I, "max_width": I, "max_height": I, "format": {"enum": ["jpeg", "png"]}, "quality": I, "settle_ms": N}), True),
    "launch_app": ("Launch an explicit argv in the selected desktop. The private desktop isolates input, not files or network permissions.", schema({"argv": {"type": "array", "items": S}}, ["argv"]), False),
    "focus_window": ("Activate a window id returned by list_windows.", schema({"window_id": S}, ["window_id"]), False),
    "focus_element": ("Focus an observed accessible element.", schema({"token": S}, ["token"]), False),
    "move": ("Move pointer. Use frame_id for screenshot pixel coordinates; otherwise use display-relative logical pixels.", schema(COORDS, ["x", "y"]), False),
    "move_relative": ("Send relative pointer motion, including locked-pointer 3D views. dx/dy are native logical deltas, independent of screenshots; positive is right/down. Focus the intended app first.", schema({"dx": N, "dy": N}, ["dx", "dy"]), False),
    "click": ("Click a coordinate or invoke the semantic action of an observed token. Double/triple clicks use count.", schema(COORDS | {"token": S, "button": {"enum": list(BUTTONS)}, "count": I}), False),
    "press_key": ("Press a key/chord, e.g. Ctrl+a, Enter, Shift+Tab. Use type_text for literal Unicode.", schema({"key": S}, ["key"]), False),
    "type_text": ("Type literal Unicode into the focused field; clipboard transfer when available. paste_key can be Ctrl+Shift+v for terminals.", schema({"text": S, "paste_key": S}, ["text"]), False),
    "scroll": ("Scroll at the pointer or an optional coordinate. Positive dy is down; 15 units is one wheel step.", schema(COORDS | {"dx": N, "dy": N}, ["dy"]), False),
    "drag": ("Drag between two observed coordinates, releasing the button even if interrupted.", schema({"start_x": N, "start_y": N, "end_x": N, "end_y": N, "display": I, "frame_id": S, "duration_ms": N, "button": S}, ["start_x", "start_y", "end_x", "end_y"]), False),
    "perform_action": ("Invoke an accessible action directly, often without changing focus or the user's pointer.", schema({"token": S, "action": S}, ["token"]), False),
    "set_value": ("Set accessible editable text or a numeric value without keyboard input.", schema({"token": S, "value": {"type": ["string", "number"]}}, ["token", "value"]), False),
    "batch": ("Run an ordered, serialized batch and optionally return one final screenshot. Stops at the first failure and reports partial completion.", schema({"actions": {"type": "array", "items": schema({"tool": S, "arguments": {"type": "object"}}, ["tool"])}, "screenshot": B, "display": I}, ["actions"]), False),
    "stop": ("Cancel ongoing input, release held keys/buttons and close the owned desktop. Restart the server for a new session.", schema(), False),
}
