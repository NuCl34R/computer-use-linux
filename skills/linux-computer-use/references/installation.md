# Installation and harness integration

## Native Linux / SteamOS

Use the distribution's Python, because GObject bindings and their native
libraries are installed together. Do not create a pip-only virtual environment
and assume it contains GTK, GStreamer or AT-SPI.

Required: Python 3.10+, PyGObject (`gi`), `dbus-python`, GTK 3, GdkPixbuf,
GStreamer with introspection, AT-SPI 2 and libxkbcommon. KDE/portal capture also
uses `gst-plugin-pipewire`/`gstreamer1.0-pipewire` and video conversion plugins.

Private desktops additionally need:

| Mode | Dependencies |
|---|---|
| KWin | `kwin_wayland`, `pipewire`, WirePlumber 0.5+, `dbus-daemon`; Xwayland is optional |
| Sway | `sway`, `dbus-daemon`, `xkeyboard-config`; works with Pixman and no GPU |
| X11 | `Xvfb`, libX11, libXtst; optional Openbox/Fluxbox/Twm window manager |
| Nested Hyprland | `Hyprland`, compatible KWin parent (preferred) or Sway, accessible GPU render node |

On the tested SteamOS installation all native KWin dependencies already existed.
No `sudo`, `pacman` change, `/dev/uinput` permission, kernel module or system
service was needed. `cul.py doctor` reports what is actually available.

Typical package names on a mutable Debian/Ubuntu system are `python3-gi
python3-dbus gir1.2-gtk-3.0 gir1.2-atspi-2.0 gir1.2-gst-plugins-base-1.0
gir1.2-gstreamer-1.0 gstreamer1.0-pipewire gstreamer1.0-plugins-base at-spi2-core`.
Arch uses `python-gobject python-dbus gtk3 at-spi2-core gstreamer
gst-plugins-base gst-plugin-pipewire`. `python3-cairo` plus `python3-gi-cairo` on Debian/Ubuntu, or `python-cairo`
on Arch, is needed for the test application's custom drawing, not for the runtime's capture code.

## Install the portable skill

From the source checkout:

```sh
./install.sh --dry-run
./install.sh --skills-dir ~/.codex/skills
```

The repository installer detects distro/DE/session, Omarchy and immutable systems,
then checks native libraries and selects native or rootless-container execution.
Use `--install-deps` for an explicit package installation on supported mutable
distros, or `--runtime container --build-container` for the container route.
`--uninstall` removes an unmodified installation using its manifest.

This copies the self-contained skill and installs a `linux-computer-use` launcher
in `~/.local/bin`. It never edits a harness's existing configuration. To use a
different skill directory, pass `~/.claude/skills`, `~/.agents/skills`, or the
directory supported by that harness. An existing install requires `--update`;
the installer keeps a verified, owner-only `.tar.gz` backup under
`$XDG_DATA_HOME/linux-computer-use/backups` (normally `~/.local/share` as the
data root, overridable with `--data-dir`). Updates also migrate installer-named
legacy skill backups into archives so harnesses discover only the active skill.
Archives preserve prior files and include `backup.json` with original paths.
Extract them only into a temporary directory outside skill discovery paths when
restoring; preserve newer changes first. Check the installer's `warnings` list
for any cleanup that could not complete.

Any MCP harness can launch the same implementation:

```json
{
  "mcpServers": {
    "linux-computer-use": {
      "command": "/absolute/path/to/bin/linux-computer-use",
      "args": ["mcp"]
    }
  }
}
```

The installer prints exact paths and this configuration. The launcher chooses
the installed runtime; `install-manifest.json` records the exact command. The server supports
MCP stdio initialization, discovery, tool calls, images, and cancellation.
It does not require a harness-specific bridge. A harness without MCP can use
the persistent CLI socket interface described in SKILL.md.

## Atomic desktop without native dependencies

The skill includes `scripts/Containerfile` and `scripts/container-run.sh`; the
repository's `scripts/container-run.sh` forwards to that self-contained wrapper.
Build the runtime in rootless Podman storage, then use MCP inside that runtime:

```sh
scripts/container-run.sh build
scripts/container-run.sh mcp
```

The wrapper disables SELinux label separation for this container to avoid
relabeling the chosen work directory. The runtime mounts only that directory, uses a
read-only root with private temporary directories, and creates a private
desktop with a writable private home. The host DE is irrelevant. Set `CUL_WORKDIR` to choose the shared
files, `CUL_NETWORK=host` if the task needs network access, and `CUL_GPU=1`
only when testing a GPU compositor. Applications launched there must exist in
the image. This route does not pretend that host application processes live
inside the container. Use the native runtime to control existing host windows.

## First-run issues

- **KDE/GNOME portal dialog:** select the intended screen and grant the requested
  pointer/keyboard access. A denial or timeout ends the session; it does not
  silently fall back to another input mechanism. Optional `persist: true` asks
  the portal for a single-use restore token, stored mode 0600 and rotated.
- **XWayland is not full desktop control:** the engine refuses XTEST when the
  session is Wayland, even if `$DISPLAY` exists.
- **No remote desktop portal on wlroots:** use the advertised virtual keyboard,
  virtual pointer, data-control and screencopy protocols. A compositor may
  restrict them; a missing protocol is reported explicitly.
- **Hyprland nested startup:** current Hyprland needs a DMA-BUF-capable parent.
  Some versions of Aquamarine bind xdg-shell v6 without accepting a parent's v5.
  KWin is the preferred parent. Use Sway/Pixman for a software-only private desktop.
- **Portal or capture failure:** the runtime reports the failing interface. It
  never treats a screenshot-only or XWayland-only probe as full readiness.
- **Missing AT-SPI for an app:** some games/custom canvases and applications that
  disable accessibility expose no tree. Screenshots and input remain available.
