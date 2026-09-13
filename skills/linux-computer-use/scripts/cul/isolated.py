"""Owned compositor lifecycle. Nothing is installed in /usr or enabled at boot."""
import json
import os
import pathlib
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time


class IsolatedDesktop:
    def __init__(self, width=1280, height=800, compositor="auto", directory=None):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="cul-", dir=directory or "/tmp"))
        self.root.chmod(0o700)
        self.processes = []
        self.logs = []
        self.guardian = None
        self.original_env = dict(os.environ)
        self.env = dict(os.environ)
        self.env.update(XDG_RUNTIME_DIR=str(self.root), XDG_CONFIG_HOME=str(self.root / "config"),
                        XDG_CACHE_HOME=str(self.root / "cache"), XDG_STATE_HOME=str(self.root / "state"),
                        XDG_DATA_HOME=str(self.root / "data"),
                        XDG_SESSION_TYPE="wayland", WAYLAND_DISPLAY="cul-wayland", GDK_BACKEND="wayland",
                        QT_QPA_PLATFORM="wayland", QT_WAYLAND_DISABLE_WINDOWDECORATION="1",
                        NO_AT_BRIDGE="0", GTK_A11Y="always", QT_LINUX_ACCESSIBILITY_ALWAYS_ON="1")
        for key in ("DISPLAY", "WAYLAND_SOCKET", "DBUS_SESSION_BUS_ADDRESS", "AT_SPI_BUS_ADDRESS",
                    "SESSION_MANAGER", "HYPRLAND_INSTANCE_SIGNATURE", "SWAYSOCK", "I3SOCK",
                    "XDG_ACTIVATION_TOKEN", "DESKTOP_STARTUP_ID"):
            self.env.pop(key, None)
        if compositor == "auto":
            compositor = "kwin" if shutil.which("kwin_wayland") else "sway" if shutil.which("sway") else "xvfb"
        self.compositor = compositor
        self.width, self.height = width, height
        try:
            if os.environ.get("CUL_PRIVATE_HOME") == "1":
                private_home = self.root / "home"
                private_home.mkdir(mode=0o700)
                self.env["HOME"] = str(private_home)
            self.guardian = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).with_name("guardian.py"))],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, text=True, env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"})
            applications = self.root / "data/applications"
            applications.mkdir(parents=True)
            (applications / "local.linuxcomputeruse.Controller.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Linux Computer Use\nNoDisplay=true\nExec=python3\n")
            bus = self._launch(["dbus-daemon", "--session", "--nofork", "--print-address=1"], "dbus", pipe=True)
            if not select.select([bus.stdout], [], [], 5)[0]:
                raise TimeoutError("Private session bus did not start")
            self.env["DBUS_SESSION_BUS_ADDRESS"] = bus.stdout.readline().decode().strip()
            if not self.env["DBUS_SESSION_BUS_ADDRESS"].startswith("unix:"):
                raise RuntimeError("Private D-Bus did not return a Unix address")
            self._start_accessibility()
            if compositor == "kwin":
                self.env.update(XDG_CURRENT_DESKTOP="KDE", KWIN_WAYLAND_NO_PERMISSION_CHECKS="1")
                # Only this private child compositor exposes its privileged
                # testing protocols. The user's compositor/config is untouched.
                config = self.root / "config"
                config.mkdir()
                (config / "kxkbrc").write_text("[Layout]\nUse=true\nLayoutList=us\n")
                self._launch(["pipewire"], "pipewire")
                self._wait_path(self.root / "pipewire-0")
                self._launch(["wireplumber", "--profile=policy"], "wireplumber")
                kwin_args = ["kwin_wayland", "--virtual", "--width", str(width),
                    "--height", str(height), "--no-lockscreen", "--no-global-shortcuts", "--no-kactivities",
                    "--socket", "cul-wayland"]
                if shutil.which("Xwayland"):
                    export = self.root / "export-x11.py"
                    export.write_text(f"#!{sys.executable}\nimport os,json,pathlib\n"
                        f"pathlib.Path({str(self.root / 'x11.json')!r}).write_text(json.dumps({{k:os.environ[k] for k in ['DISPLAY','XAUTHORITY'] if k in os.environ}}))\n")
                    export.chmod(0o700)
                    kwin_args += ["--xwayland", str(export)]
                self.compositor_process = self._launch(kwin_args, "compositor")
                self._wait_path(self.root / "cul-wayland")
                if shutil.which("Xwayland"):
                    self._wait_path(self.root / "x11.json")
                    self.env.update(json.loads((self.root / "x11.json").read_text()))
            elif compositor == "sway":
                self.env.update(XDG_CURRENT_DESKTOP="sway", WLR_BACKENDS="headless", WLR_LIBINPUT_NO_DEVICES="1",
                                WLR_RENDERER="pixman", WLR_HEADLESS_OUTPUTS="1")
                config = self.root / "sway.conf"
                config.write_text(f"output * mode {width}x{height}\nseat seat0 fallback true\n"
                                  "default_border none\nfocus_follows_mouse no\n")
                self.compositor_process = self._launch(["sway", "--config", str(config)], "compositor")
                self._find_wayland()
            elif compositor == "hyprland":
                parent_display = self._hyprland_parent(width, height)
                # Hyprland currently requires a DRM allocator even for headless
                # outputs. Nest it in an owned compositor with DMA-BUF support.
                # Force seatd to a nonexistent private socket: no host seat/DRM
                # takeover can occur before its Wayland fallback is selected.
                self.env.update(XDG_CURRENT_DESKTOP="Hyprland", WAYLAND_DISPLAY=parent_display,
                                LIBSEAT_BACKEND="seatd", SEATD_SOCK=str(self.root / "no-seatd.sock"), AQ_NO_MODIFIERS="1")
                config = self.root / "hyprland.conf"
                config.write_text(f"monitor = ,{width}x{height}@60,0x0,1\n"
                    "debug {\n disable_logs = false\n enable_stdout_logs = true\n}\n"
                    "animations {\n enabled = false\n}\nmisc {\n disable_hyprland_logo = true\n"
                    " disable_splash_rendering = true\n}\ninput {\n kb_layout = us\n}\n")
                self.compositor_process = self._launch(["Hyprland", "--config", str(config)], "compositor")
                self._find_wayland()
            elif compositor == "xvfb":
                self.env.update(XDG_CURRENT_DESKTOP="CUL-X11", XDG_SESSION_TYPE="x11", GDK_BACKEND="x11", QT_QPA_PLATFORM="xcb")
                self.env.pop("WAYLAND_DISPLAY", None)
                auth = self.root / "Xauthority"
                cookie = os.urandom(16).hex()
                # Xauthority's wildcard family authenticates our displayfd-selected display.
                import struct
                def field(value):
                    return struct.pack(">H", len(value)) + value
                auth.write_bytes(struct.pack(">H", 65535) + field(b"") + field(b"") +
                                 field(b"MIT-MAGIC-COOKIE-1") + field(bytes.fromhex(cookie)))
                auth.chmod(0o600)
                self.env["XAUTHORITY"] = str(auth)
                self.compositor_process = self._launch(["Xvfb", "-displayfd", "1", "-screen", "0",
                    f"{width}x{height}x24", "-nolisten", "tcp", "-auth", str(auth)], "compositor", pipe=True)
                if not select.select([self.compositor_process.stdout], [], [], 8)[0]:
                    raise TimeoutError("Xvfb did not start")
                number = self.compositor_process.stdout.readline().decode().strip()
                if not number.isdigit():
                    raise RuntimeError("Xvfb did not report a display")
                self.env["DISPLAY"] = ":" + number
                for wm in ("openbox", "fluxbox", "twm"):
                    if shutil.which(wm):
                        self._launch([wm], "wm")
                        break
            else:
                raise ValueError("compositor must be auto, kwin, sway, hyprland, or xvfb")
            (self.root / "session.json").write_text(json.dumps({"compositor": compositor, "width": width,
                "height": height, "pids": [p.pid for p in self.processes]}))
            (self.root / "environment.json").write_text(json.dumps({k: self.env[k] for k in
                ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "AT_SPI_BUS_ADDRESS", "XAUTHORITY") if k in self.env}))
        except BaseException:
            self.close()
            raise

    def _launch(self, argv, name, pipe=False, environment=None):
        if not shutil.which(argv[0]):
            raise RuntimeError(f"Missing executable {argv[0]}; see references/installation.md")
        log = open(self.root / (name + ".log"), "ab", buffering=0)
        self.logs.append(log)
        process = subprocess.Popen(argv, env=environment or self.env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if pipe else log, stderr=log, start_new_session=True)
        self.processes.append(process)
        from .guardian import birth
        start = birth(process.pid)
        if start is not None:
            self.guardian.stdin.write(json.dumps({"pid": process.pid, "start": start}) + "\n")
            self.guardian.stdin.flush()
        return process

    def _hyprland_parent(self, width, height):
        parent = self.root / "parent"
        parent.mkdir(mode=0o700)
        env = dict(self.env, XDG_RUNTIME_DIR=str(parent))
        env.pop("WAYLAND_DISPLAY", None)
        if shutil.which("kwin_wayland"):
            env["XDG_CURRENT_DESKTOP"] = "KDE"
            process = self._launch(["kwin_wayland", "--virtual", "--width", str(width), "--height", str(height),
                "--no-lockscreen", "--no-global-shortcuts", "--no-kactivities", "--socket", "cul-parent"],
                "parent-compositor", environment=env)
            candidates = lambda: [parent / "cul-parent"] if (parent / "cul-parent").exists() else []
        elif shutil.which("sway"):
            nodes = sorted(pathlib.Path("/dev/dri").glob("renderD*"))
            if not nodes:
                raise RuntimeError("Nested Hyprland requires a GPU render node and KWin or Sway; use compositor=sway or xvfb for software-only isolation")
            env.update(XDG_CURRENT_DESKTOP="sway", WLR_BACKENDS="headless", WLR_LIBINPUT_NO_DEVICES="1",
                       WLR_RENDERER="gles2", WLR_RENDER_DRM_DEVICE=str(nodes[0]))
            config = parent / "sway.conf"
            config.write_text(f"output * mode {width}x{height}\nseat seat0 fallback true\ndefault_border none\n")
            process = self._launch(["sway", "--unsupported-gpu", "--config", str(config)], "parent-compositor", environment=env)
            candidates = lambda: [p for p in parent.glob("wayland-*") if not p.name.endswith(".lock")]
        else:
            raise RuntimeError("Nested Hyprland needs kwin_wayland or sway; use compositor=auto for the portable isolated desktop")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            paths = candidates()
            if paths:
                return str(paths[0])
            if process.poll() is not None:
                raise RuntimeError(f"Hyprland parent compositor exited; see {self.root / 'parent-compositor.log'}")
            time.sleep(.05)
        raise TimeoutError("Hyprland parent compositor startup timed out")

    def _start_accessibility(self):
        registry = next((p for p in ("/usr/lib/at-spi2-registryd", "/usr/libexec/at-spi2-registryd",
            "/usr/lib/at-spi2-core/at-spi2-registryd") if pathlib.Path(p).exists()), None)
        if registry is None:
            return  # Pixel control remains available; doctor/get_state reports missing AT-SPI.
        a11y = self._launch(["dbus-daemon", "--session", "--nofork", "--print-address=1"], "a11y-dbus", pipe=True)
        if not select.select([a11y.stdout], [], [], 5)[0]:
            raise TimeoutError("Private accessibility bus did not start")
        self.env["AT_SPI_BUS_ADDRESS"] = a11y.stdout.readline().decode().strip()
        proxy = self._launch([sys.executable, str(pathlib.Path(__file__).with_name("a11y_bus.py")),
                              self.env["AT_SPI_BUS_ADDRESS"]], "a11y-address", pipe=True)
        if not select.select([proxy.stdout], [], [], 5)[0] or proxy.stdout.readline().strip() != b"ready":
            raise RuntimeError(f"Private accessibility address service failed; see {self.root}")
        self._launch([registry], "a11y-registry")

    def _wait_path(self, path):
        deadline = time.monotonic() + 15
        while not path.exists():
            if any(p.poll() is not None for p in self.processes):
                raise RuntimeError(f"A private desktop process exited; see {self.root}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"Private desktop did not create {path}; see {self.root}")
            time.sleep(.05)

    def _find_wayland(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            candidates = [p for p in self.root.glob("wayland-*") if not p.name.endswith(".lock")]
            if candidates:
                self.env["WAYLAND_DISPLAY"] = candidates[0].name
                return
            if self.compositor_process.poll() is not None:
                raise RuntimeError(f"Private compositor exited; see {self.root / 'compositor.log'}")
            time.sleep(.05)
        raise TimeoutError(f"Compositor startup timed out; see {self.root}")

    def activate_environment(self):
        os.environ.clear()
        os.environ.update(self.env)

    def launch_app(self, argv):
        return self._launch(argv, f"app-{len(self.processes)}").pid

    def close(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=2)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=2)
                    except ProcessLookupError:
                        pass
        for log in self.logs:
            log.close()
        self.processes.clear()
        if self.guardian:
            self.guardian.stdin.close()
            self.guardian.wait(timeout=3)
            self.guardian = None
        os.environ.clear()
        os.environ.update(self.original_env)
