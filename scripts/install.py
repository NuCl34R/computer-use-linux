#!/usr/bin/env python3
"""Detect Linux capabilities and install a portable, per-user computer-use runtime."""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
NAME = "linux-computer-use"
IDENTITY = "local.linuxcomputeruse.Controller.desktop"
IMAGE = "localhost/linux-computer-use:0.1.0"
PACKAGES = {
    "apt-get": "python3 python3-gi python3-dbus python3-cairo gir1.2-gtk-3.0 gir1.2-atspi-2.0 gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-pipewire at-spi2-core dbus-daemon libxkbcommon0 libxtst6 sway xwayland x11-xkb-utils".split(),
    "pacman": "python python-gobject python-dbus python-cairo gtk3 at-spi2-core gstreamer gst-plugins-base gst-plugins-good gst-plugin-pipewire dbus libxkbcommon libxtst sway xorg-xwayland xkeyboard-config".split(),
    "dnf": "python3 python3-gobject python3-dbus python3-cairo gtk3 at-spi2-core gstreamer1 gstreamer1-plugins-base gstreamer1-plugins-good pipewire-gstreamer dbus-daemon libxkbcommon libXtst sway xorg-x11-server-Xwayland xkeyboard-config".split(),
    "zypper": "python3 python3-gobject python3-gobject-Gdk python3-dbus-python python3-cairo typelib-1_0-Gtk-3_0 typelib-1_0-Atspi-2_0 typelib-1_0-Gst-1_0 typelib-1_0-GstVideo-1_0 gstreamer-plugins-base gstreamer-plugins-good pipewire-spa-plugins-0_2 gstreamer-plugin-pipewire at-spi2-core dbus-1 libxkbcommon0 libXtst6 sway xwayland xkeyboard-config".split(),
}
PROBE = r'''
import ctypes.util, json
missing = []
try:
 import dbus
except ImportError:
 missing.append("dbus-python")
try:
 import gi
 for namespace, version in (("Gtk", "3.0"), ("GdkPixbuf", "2.0"), ("Gst", "1.0"), ("GstVideo", "1.0"), ("Atspi", "2.0")):
  try:
   gi.require_version(namespace, version)
   __import__("gi.repository", fromlist=[namespace]).__getattribute__(namespace)
  except (ValueError, ImportError, AttributeError) as e:
   missing.append(namespace + ": " + str(e))
 from gi.repository import Gst
 Gst.init(None)
 gst = {name: bool(Gst.ElementFactory.find(name)) for name in ("pipewiresrc", "videoconvert", "appsink")}
except (ImportError, ValueError):
 missing.append("PyGObject / GStreamer introspection")
 gst = {}
for name in ("xkbcommon", "wayland-client", "X11", "Xtst"):
 if not ctypes.util.find_library(name):
  missing.append("lib" + name)
print(json.dumps({"missing": missing, "gstreamer": gst}))
'''


def os_release(path=pathlib.Path("/etc/os-release")):
    """Parse as data; never source os-release as shell code."""
    result = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return result
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.isidentifier():
            try:
                tokens = shlex.split(value, comments=True)
                result[key] = " ".join(tokens)
            except ValueError:
                pass
    return result


def detect(release=None, env=None, exists=None, which=None, read_only=None):
    release = os_release() if release is None else release
    env = os.environ if env is None else env
    exists = (lambda value: pathlib.Path(value).exists()) if exists is None else exists
    which = shutil.which if which is None else which
    ids = (release.get("ID", "unknown") + " " + release.get("ID_LIKE", "")).lower().split()
    variant = release.get("VARIANT_ID", "").lower()
    if read_only is None:
        read_only = bool(os.statvfs("/usr").f_flag & os.ST_RDONLY)
    atomic_reasons = []
    if read_only:
        atomic_reasons.append("/usr is read-only")
    for marker in ("/run/ostree-booted", "/ostree/repo", "/usr/share/ublue-os", "/run/bootc"):
        if exists(marker):
            atomic_reasons.append(marker)
    if any(value in ids for value in ("steamos", "bazzite", "bluefin", "aurora", "silverblue", "kinoite", "aeon", "kalpa", "opensuse-aeon", "opensuse-kalpa", "opensuse-microos", "nixos")) or variant in ("silverblue", "kinoite", "sericea", "sway-atomic", "onyx", "atomic", "cosmic-atomic"):
        atomic_reasons.append("image-based/declarative distribution")
    if which("transactional-update"):
        atomic_reasons.append("transactional-update")
    family = next((name for names, name in ((["arch", "steamos", "manjaro", "endeavouros", "omarchy"], "arch"), (["debian", "ubuntu", "linuxmint", "pop"], "debian"), (["fedora", "rhel", "centos"], "fedora"), (["suse", "opensuse", "opensuse-tumbleweed", "opensuse-leap"], "suse")) if any(x in ids for x in names)), "unknown")
    manager = {"arch": "pacman", "debian": "apt-get", "fedora": "dnf", "suse": "zypper"}.get(family)
    manager = manager if manager and which(manager) else None
    desktop = env.get("XDG_CURRENT_DESKTOP", env.get("XDG_SESSION_DESKTOP", "unknown"))
    if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        desktop = "Hyprland"
    elif env.get("SWAYSOCK"):
        desktop = "Sway"
    elif env.get("KDE_FULL_SESSION") == "true":
        desktop = "KDE"
    omarchy_path = pathlib.Path(env.get("OMARCHY_PATH", str(pathlib.Path.home() / ".local/share/omarchy")))
    omarchy = bool(which("omarchy-version") or exists(str(omarchy_path / "version")))
    session = env.get("XDG_SESSION_TYPE") or ("wayland" if env.get("WAYLAND_DISPLAY") else "x11" if env.get("DISPLAY") else "headless")
    return {"distribution": release.get("PRETTY_NAME", release.get("ID", "Unknown Linux")), "id": ids[0], "version": release.get("VERSION_ID"), "family": family,
        "desktop": desktop, "session": session, "architecture": platform.machine(), "omarchy": omarchy,
        "atomic": bool(atomic_reasons), "atomic_reasons": atomic_reasons, "package_manager": manager}


def capabilities():
    try:
        probe = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=20)
        result = json.loads(probe.stdout) if probe.returncode == 0 else {"missing": ["Native library probe failed: " + probe.stderr[-500:]], "gstreamer": {}}
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        result = {"missing": [str(error)], "gstreamer": {}}
    result["commands"] = {name: shutil.which(name) for name in ("dbus-daemon", "kwin_wayland", "pipewire", "wireplumber", "sway", "Hyprland", "Xvfb", "podman")}
    result["wireplumber_compatible"] = False
    if result["commands"]["wireplumber"]:
        try:
            output = subprocess.run([result["commands"]["wireplumber"], "--version"], capture_output=True, text=True, timeout=5).stdout
            import re
            match = re.search(r"(\d+)\.(\d+)", output)
            result["wireplumber_compatible"] = bool(match and tuple(map(int, match.groups())) >= (0, 5))
        except (OSError, subprocess.TimeoutExpired):
            pass
    return result


def make_plan(system, caps, requested="auto"):
    commands = caps["commands"]
    compositor = None
    if all(commands.get(name) for name in ("kwin_wayland", "pipewire", "wireplumber")) and caps.get("wireplumber_compatible") and all(caps.get("gstreamer", {}).get(name) for name in ("pipewiresrc", "videoconvert", "appsink")):
        compositor = "kwin"
    elif commands.get("sway"):
        compositor = "sway"
    elif commands.get("Xvfb"):
        compositor = "xvfb"
    missing = list(caps["missing"])
    if not commands.get("dbus-daemon"):
        missing.append("dbus-daemon")
    if not compositor:
        missing.append("a usable private compositor (KWin + PipeWire/WirePlumber 0.5+, Sway, or Xvfb)")
    runtime = requested
    if runtime == "auto":
        runtime = "native" if not missing or (not system["atomic"] and system["package_manager"]) else "container"
    manager = system["package_manager"]
    dependency_command = None
    if runtime == "native" and missing and manager and not system["atomic"]:
        options = {"apt-get": ["install"], "pacman": ["-S", "--needed"], "dnf": ["install"], "zypper": ["install"]}[manager]
        dependency_command = ["sudo", manager, *options, *PACKAGES[manager]]
    return {"system": system, "python": sys.executable, "runtime": runtime,
        "compositor": compositor if runtime == "native" else "sway", "missing_native": missing,
        "dependency_command": dependency_command, "podman_available": bool(commands.get("podman")),
        "container_image": IMAGE if runtime == "container" else None,
        "note": "Detection is not E2E certification. Omarchy workstation validation is pending."}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(args, plan):
    destination = args.skills_dir.expanduser().absolute() / NAME
    launcher = args.bin_dir.expanduser().absolute() / NAME
    desktop = args.data_dir.expanduser().absolute() / "applications" / IDENTITY
    targets = [destination, launcher, desktop]
    if any(p.is_symlink() for p in targets):
        raise RuntimeError("An installation target is a symlink; choose a regular destination")
    if any(p.exists() for p in targets) and not args.update:
        raise RuntimeError("An installation target already exists; use --update to keep a backup and replace it")
    if len(set(targets)) != 3 or any(a in b.parents for a in targets for b in targets if a != b):
        raise RuntimeError("Skill, launcher and application identity paths must not overlap")
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
    staged = []
    backups = []
    committed = []
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        stage = pathlib.Path(tempfile.mkdtemp(prefix=".cul-stage-", dir=destination.parent))
        staged.append(stage)
        shutil.copytree(ROOT / "skills" / NAME, stage, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if plan["runtime"] == "native":
            command = [sys.executable, str(destination / "scripts/cul.py")]
            prefix = "export CUL_DEFAULT_COMPOSITOR=" + shlex.quote(plan["compositor"]) + "\n"
        else:
            command = [str(destination / "scripts/container-run.sh")]
            prefix = ""
        launcher_text = "#!/bin/sh\n" + prefix + "exec " + shlex.join(command) + ' "$@"\n'
        desktop_text = "[Desktop Entry]\nType=Application\nName=Linux Computer Use\nNoDisplay=true\nExec=python3\n"
        for path, content, mode in ((launcher, launcher_text, 0o755), (desktop, desktop_text, 0o644)):
            fd, temp = tempfile.mkstemp(prefix=".cul-stage-", dir=path.parent)
            temp = pathlib.Path(temp)
            staged.append(temp)
            with os.fdopen(fd, "w") as output:
                output.write(content)
            temp.chmod(mode)
        configuration = {"mcpServers": {NAME: {"command": str(launcher), "args": ["mcp"]}}}
        manifest = {"schema": 1, "runtime": plan["runtime"], "compositor": plan["compositor"], "paths": [str(p) for p in targets],
            "mcp_configuration": configuration, "external_sha256": {str(p): digest(temp) for p, temp in zip(targets[1:], staged[1:])},
            "files_sha256": {str(p.relative_to(stage)): digest(p) for p in sorted(stage.rglob("*")) if p.is_file()}}
        (stage / "install-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        for path, temporary in zip(targets, staged):
            if path.exists():
                backup = path.with_name(path.name + ".backup-" + stamp)
                path.rename(backup)
                backups.append((path, backup))
            temporary.rename(path)
            committed.append(path)
        return {"installed": True, "plan": plan, "skill": str(destination / "SKILL.md"), "launcher": str(launcher),
            "mcp_configuration": configuration, "backups": [str(p) for _, p in backups],
            "path_hint": "Add " + str(launcher.parent) + " to PATH if needed; no shell configuration was edited."}
    except BaseException:
        for path in reversed(committed):
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        for path, backup in reversed(backups):
            backup.rename(path)
        raise
    finally:
        for path in staged:
            if path.exists():
                shutil.rmtree(path) if path.is_dir() else path.unlink()


def uninstall(args):
    destination = args.skills_dir.expanduser().absolute() / NAME
    expected = [destination, args.bin_dir.expanduser().absolute() / NAME, args.data_dir.expanduser().absolute() / "applications" / IDENTITY]
    manifest = json.loads((destination / "install-manifest.json").read_text())
    if manifest.get("schema") != 1 or manifest.get("paths") != [str(p) for p in expected]:
        raise RuntimeError("Installation paths do not match; pass the original directory options")
    for path in expected:
        if path.is_symlink():
            raise RuntimeError("Refusing to remove a symlink installation target")
    actual = {str(p.relative_to(destination)): digest(p) for p in destination.rglob("*") if p.is_file() and p.name != "install-manifest.json" and "__pycache__" not in p.parts}
    if actual != manifest["files_sha256"]:
        raise RuntimeError("Installed skill was modified; preserve your changes and remove it manually")
    for path in expected[1:]:
        if path.exists() and digest(path) != manifest["external_sha256"][str(path)]:
            raise RuntimeError("Installed file was modified: " + str(path))
    if not args.dry_run:
        for path in expected[1:]:
            path.unlink(missing_ok=True)
        shutil.rmtree(destination)
    return {"uninstalled": not args.dry_run, "remove": [str(p) for p in expected], "note": "Backups and Podman images are retained."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=["codex", "claude", "agents"], default="codex")
    parser.add_argument("--skills-dir", type=pathlib.Path, help="Overrides --harness; any harness skill directory")
    parser.add_argument("--bin-dir", type=pathlib.Path, default=pathlib.Path.home() / ".local/bin")
    parser.add_argument("--data-dir", type=pathlib.Path, default=pathlib.Path(os.environ.get("XDG_DATA_HOME", str(pathlib.Path.home() / ".local/share"))))
    parser.add_argument("--runtime", choices=["auto", "native", "container"], default="auto")
    parser.add_argument("--dry-run", "--plan", action="store_true", help="Print detection and commands; do not install")
    parser.add_argument("--install-deps", action="store_true", help="Run the displayed distribution package command on a mutable system")
    parser.add_argument("--build-container", action="store_true", help="Build the Podman runtime if container mode is selected")
    parser.add_argument("--update", action="store_true", help="Replace installed files, keeping timestamped backups")
    parser.add_argument("--uninstall", action="store_true", help="Remove an unmodified installation using its manifest")
    args = parser.parse_args()
    if args.skills_dir is None:
        args.skills_dir = pathlib.Path.home() / {"codex": ".codex/skills", "claude": ".claude/skills", "agents": ".agents/skills"}[args.harness]
    if platform.system() != "Linux" or sys.version_info < (3, 10):
        parser.error("Linux with system Python 3.10+ is required")
    try:
        if args.uninstall:
            print(json.dumps(uninstall(args), indent=2))
            return 0
        plan = make_plan(detect(), capabilities(), args.runtime)
        if args.dry_run:
            print(json.dumps(plan, indent=2))
            return 0
        print(json.dumps(plan, indent=2), file=sys.stderr)
        if plan["runtime"] == "native" and plan["missing_native"]:
            if not args.install_deps or not plan["dependency_command"]:
                raise RuntimeError("Native dependencies are missing. Review --dry-run; use --install-deps on a supported mutable distro, or --runtime container --build-container with rootless Podman. Atomic OS images are never modified.")
            if plan["system"]["package_manager"] == "pacman":
                print("Arch: keep your system fully updated first. This installs packages using existing sync databases; it does not refresh databases or upgrade the OS.", file=sys.stderr)
            subprocess.run(plan["dependency_command"], check=True, stdout=sys.stderr)
            plan = make_plan(detect(), capabilities(), "native")
            if plan["missing_native"]:
                raise RuntimeError("Dependencies still missing after package installation: " + ", ".join(plan["missing_native"]))
        if plan["runtime"] == "container":
            if not plan["podman_available"]:
                raise RuntimeError("Install rootless Podman using your distribution's supported method, then rerun --runtime container --build-container. No OS image changes were attempted.")
            podman_info = subprocess.run(["podman", "info", "--format", "{{.Host.Security.Rootless}}"], check=True, capture_output=True, text=True, timeout=30)
            if podman_info.stdout.strip() != "true":
                raise RuntimeError("Rootless Podman is required; run as your normal user")
            if args.build_container:
                subprocess.run([str(ROOT / "scripts/container-run.sh"), "build"], check=True, stdout=sys.stderr)
            if subprocess.run(["podman", "image", "exists", IMAGE]).returncode:
                raise RuntimeError("Runtime image is missing; rerun with --runtime container --build-container")
        print(json.dumps(install(args, plan), indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"installed": False, "error": str(error)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
