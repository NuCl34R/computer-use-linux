# Changelog

## 0.2.0 — Preview — 2026-09-13

- Add an on-demand local browser spectator for MCP and CLI sessions, including
  container sessions: live frames, display selection, pause view and confirmed
  session stop. Closing the view leaves the agent running.
- Keep spectator capture independent of agent frame IDs and active input,
  using dedicated display connections or the existing PipeWire sample.
- Protect spectator sockets by Linux UID and local browser APIs by a random
  token, Host and Origin checks. No desktop input or application-launch API is
  exposed through the viewer.
- Prepare the rootless runtime after image builds, keeping first-run image
  preparation outside the harness's MCP startup deadline. Add `prepare` for
  custom images and preserve diagnostic signal forwarding.

- Keep repository documentation focused on the standalone Linux skill. Replace
  the dedicated Omarchy workstation guide and roadmap with general desktop
  validation guidance; retain Arch/Hyprland environment detection.

## 0.1.1 — Preview — 2026-09-13

- Store verified, private backup archives outside skill discovery directories;
  migrate installer-created legacy skill backups during `--update`.
- Preserve user edits, file modes and symlinks in archives; retain transactional
  rollback and test repeated updates, migration and backup write failures.

- Native `move_relative` input for locked-pointer 3D cameras on KWin, wlroots,
  X11 and RemoteDesktop portals; twenty shared tools.
- Relative-pointer validation through native GTK callbacks on four backends,
  with input contracts and installed-launcher coverage.

## 0.1.0 — Preview — 2026-09-13

### Native desktop runtime

- Nineteen shared MCP/JSON CLI tools for observation, input, accessibility, applications and session control.
- Private KWin, Sway, Hyprland and Xvfb desktops, with independent input and clipboard.
- KDE portal, wlroots protocol and X11 input/capture paths.
- Unicode text, scaled screenshot coordinates, native window handling, semantic actions and bounded AT-SPI queries.
- Persistent capture, changed-frame verification, cancellation and independent crash cleanup.
- Rootless Podman runtime for immutable hosts.

### Distribution and repository

- Universal local installer with distro/DE/session/Omarchy/immutable detection and native/container selection.
- Explicit native dependency recipes, custom harness paths, transactional updates with backups and manifest-checked removal.
- Generated README masthead, original architecture diagram, real GUI evidence, English landing page and French guide.
- Installer contracts, GitHub Actions Sway/Xvfb E2E, documentation checks and checksummed source packaging.

### Validation boundaries

- Thirteen-report initial Linux E2E baseline plus installation-specific validation.
- Omarchy workstation acceptance remains pending.
- No macOS/Windows performance comparison or individual certification of every Linux distribution.
