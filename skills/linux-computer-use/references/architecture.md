# Architecture and scope

One persistent process owns the desktop session. Both transports call the same
serialized engine. Input is not spawned through a shell for every keystroke.
The CLI Unix socket is mode 0600 in a mode 0700 directory and verifies peer UID;
the MCP transport uses stdio. There is no network listener.

| Backend | Observation | Input | Window discovery |
|---|---|---|---|
| Current KDE/GNOME portal | One authorized PipeWire stream per selected display | RemoteDesktop keyboard/pointer; Clipboard when granted, Wayland data-control on KDE otherwise | KWin scripting; AT-SPI apps elsewhere |
| Private KWin | Persistent PipeWire stream | Private KWin fake-input protocol, private clipboard | KWin scripting |
| Hyprland/Sway | wlroots SHM screencopy | Virtual keyboard/pointer; data-control clipboard | Hyprland/Sway native IPC |
| X11/Xvfb | XGetImage over an open X connection | XTEST; GTK clipboard | EWMH/Xlib |

AT-SPI supplies bounded semantic trees, direct button actions, editable text,
numeric values and focus. Traversal is capped by node count, depth and a time
budget, with per-call timeouts. Element references are process-local and expire.
All AT-SPI operations and cache events run on the same GLib thread; its native
cache is not safe to read concurrently with event dispatch.
Password-role text is excluded. UI strings cannot become executable compositor
scripts: arguments are encoded as JSON and callback senders are verified.

KWin private mode creates its own runtime/config/cache directories, D-Bus,
AT-SPI address service/registry, PipeWire and policy-only WirePlumber. The
privileged KWin test interfaces are enabled only in that owned compositor.
The live compositor is never reconfigured. Private applications inherit their
display and bus explicitly, overriding misleading ambient GTK/X11 settings.
An independent guardian holds a pipe lease and registers only the private child
process groups with their Linux start times. EOF, a native crash or SIGKILL lets
it reclaim those groups without matching or killing unrelated host processes.
Normal shutdown also detaches AT-SPI, D-Bus and GTK connections before stopping
their private services. GIO/libdbus exit-on-disconnect is disabled for these
owned connections, and GLib cleanup runs on its event thread.

PipeWire frames are copied out of its buffer pool promptly, converted by native
GStreamer, then encoded on demand with native GdkPixbuf. This avoids reopening
portals or launching screenshot programs for each action. Preview payloads have
a 4 MiB cap and explicit image/logical-size mappings. A latest-frame read is
different from action-to-visible-repaint latency; benchmark them separately.

Screencopy on wlroots is currently an on-demand SHM capture over a persistent
Wayland connection, not a continuous video stream. GPU DMA-BUF zero-copy,
libei-based input, OCR, browser DOM/CDP integration, GNOME Shell window focusing,
and arbitrary DE-specific window-management APIs are not implemented here.
They are not required for the tested pixel/AT-SPI control, but are future work.
Restart the session after monitor hotplug or changes to output topology. On a
portal compositor without Clipboard or Wayland data-control, non-ASCII typing
fails explicitly; accessible fields can still use `set_value`.

Display separation does not isolate files or network. The private applications
are same-user applications. For file isolation, run the supplied Podman image
with only the work directory mounted. Existing single-instance applications
may need a fresh profile/instance argument to stay in the private session.

## Native protocol specifications

- [RemoteDesktop portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)
  and [ScreenCast portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html).
- [KDE Wayland protocols](https://invent.kde.org/libraries/plasma-wayland-protocols)
  and [KWin source](https://invent.kde.org/plasma/kwin).
- [wlroots protocols](https://gitlab.freedesktop.org/wlroots/wlr-protocols)
  and [Hyprland protocols](https://github.com/hyprwm/Hyprland/tree/main/protocols).
- [Aquamarine backend source](https://github.com/hyprwm/aquamarine/blob/main/src/backend/Backend.cpp)
  explains the allocator requirement for nested Hyprland.

This implementation is original Python code using the native protocols and
installed libraries; no third-party driver assets are redistributed.
