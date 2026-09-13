# Installation guide

[← Repository home](../README.md) · [Omarchy](OMARCHY.md) · [Agent instructions](../skills/linux-computer-use/SKILL.md)

## What the installer does

`./install.sh` resolves the source checkout and uses `/usr/bin/python3` by default, avoiding an unrelated virtual environment on PATH. Set `CUL_PYTHON=/absolute/path/python3` to override it. A system Python 3.10+ is required; the installer uses only the Python standard library. Runtime GI bindings are distribution packages.

1. Reads `/etc/os-release` as data; detects `ID`/`ID_LIKE`, desktop/session environment, Omarchy installation markers and architecture.
2. Detects SteamOS, OSTree/bootc markers, Fedora Atomic variants, Universal Blue variants, transactional openSUSE, NixOS and a read-only `/usr`.
3. Probes Python GI namespaces, D-Bus, native libraries, GStreamer factories and compositor executables without opening a desktop.
4. Selects complete native dependencies first. On mutable supported systems it offers a native package recipe; otherwise it chooses the container route.
5. Installs the complete skill, a launcher and a portal application identity under your home directory, then prints exact MCP configuration.

`--dry-run` prints a JSON plan and does not install files, run a package manager, build an image or open a portal. Detection does not certify GUI behavior; run the E2E tests to establish that.

## Distribution routing

| Family / system | Native recipe | Immutable behavior |
| --- | --- | --- |
| Arch, Omarchy, Manjaro, EndeavourOS | `sudo pacman -S --needed …` | SteamOS never gets a package command |
| Debian, Ubuntu and derivatives | `sudo apt-get install …` | Read-only roots route around system changes |
| Fedora and compatible systems | `sudo dnf install …` | Atomic/OSTree/Universal Blue use existing dependencies or Podman |
| openSUSE | `sudo zypper install …` | Aeon/Kalpa/MicroOS/transactional hosts use existing dependencies or Podman |
| NixOS | No imperative native package recipe | Use a declarative native setup or rootless Podman |
| Unknown Linux | Probe existing native capabilities | Container fallback; no guessed package command |

Recipes are checked for capability completeness after the package manager returns. They are release-dependent recipes, not individually certified distro installers. Update package metadata using your normal distribution procedure if packages cannot be found. The installer deliberately does not run an OS upgrade. On Arch, a fully updated system avoids unsupported partial-upgrade combinations.

Known Omarchy markers are `omarchy-version` on PATH or the `version` file under `$OMARCHY_PATH` (default `~/.local/share/omarchy`). Omarchy is treated as an Arch environment, with no edits to its Hyprland configuration or update system. This follows [Omarchy’s own version command](https://github.com/basecamp/omarchy/blob/master/bin/omarchy-version).

Native package naming follows [PyGObject’s distribution guidance](https://pygobject.gnome.org/getting_started.html), the [Ubuntu package archive](https://packages.ubuntu.com/noble/sway), [Fedora PipeWire packaging](https://packages.fedoraproject.org/pkgs/pipewire/pipewire-gstreamer/) and the corresponding distribution repositories. Additional backend requirements are listed in the [portable dependency reference](../skills/linux-computer-use/references/installation.md).

## Installer options

```sh
./install.sh --help
./install.sh --dry-run
./install.sh --runtime native --install-deps
./install.sh --runtime container --build-container
./install.sh --harness claude
./install.sh --skills-dir /custom/skills --bin-dir /custom/bin --data-dir /custom/share
./install.sh --update
./install.sh --uninstall --dry-run
./install.sh --uninstall
```

`--install-deps` explicitly enables the displayed native package command on a mutable system; the package manager asks for normal confirmation. It never enables package installation on an immutable host. Podman is a bootstrap dependency for container mode and must be configured through the distribution’s supported method. Run the installer as the intended normal user, not through sudo.

An installation is staged before replacement. Updating creates and verifies a timestamped `.tar.gz` backup in `$XDG_DATA_HOME/linux-computer-use/backups` (normally `~/.local/share/linux-computer-use/backups`; overridden by `--data-dir`). Archives have owner-only read/write permissions. They contain the previous skill, launcher, application identity and a `backup.json` with original paths and file checksums. Keeping backups as archives prevents harnesses from discovering duplicate skills. `--update` also migrates installer-named legacy skill backup directories into this archive storage, preserving their contents before removing the old directories. Unrecognized directories and symlinks are left alone; cleanup failures are reported in the installer's `warnings` list.

The installer rolls installed files back on a write/rename failure. OS package operations and container image builds occur before this transaction and are not rolled back. The manifest `install-manifest.json` stores file checksums, chosen runtime and exact MCP configuration.

Removal checks the manifest and refuses to delete modified installed files. Pass the same harness/path options used for installation. It leaves backups, user work and Podman images intact. To restore, extract a trusted archive into a temporary directory outside your harness's skill directories, inspect `backup.json`, and restore the listed entries to their original paths after preserving newer changes. A migrated legacy archive contains only that legacy skill directory. Do not extract backups alongside active skills: this would create discoverable duplicates again.

The launcher directory may need to be added to PATH using your normal shell configuration. Until then use `~/.local/bin/linux-computer-use` explicitly.

## Container runtime

```sh
./install.sh --runtime container --build-container
mkdir -p "$HOME/Projects/agent-work"
CUL_WORKDIR="$HOME/Projects/agent-work" ~/.local/bin/linux-computer-use mcp
```

The image contains KWin, Hyprland, Sway, Xvfb, GTK, Qt’s kdialog, xterm and native libraries. It defaults to **Sway/Pixman**, so no GPU device is needed. It uses a private home inside its temporary filesystem and a read-only image root. It is not a complete copy of the host desktop.

| Variable | Default | Meaning |
| --- | --- | --- |
| `CUL_WORKDIR` | Launching process’s current directory | Host directory mounted read/write at `/work` |
| `CUL_NETWORK` | `none` | Set `host` when the task needs network access |
| `CUL_IMAGE` | `localhost/linux-computer-use:0.1.1` | Alternative locally built runtime image |
| `CUL_GPU` | `0` | Set `1` to expose `/dev/dri/renderD128` for a GPU compositor |

Set these through the harness’s environment configuration for MCP. Choose an explicit work directory instead of relying on an unknown harness working directory. There is no host home, display, session bus, input device or container-engine socket mount. SELinux label separation is disabled for this container so mounting a user work directory does not relabel it; normal user permissions still apply.

Network is disabled during sessions by default; the **image build** needs network access for distribution packages. The image’s Arch base is pinned by digest, but package versions come from the repositories at build time. Capture `podman image inspect localhost/linux-computer-use:0.1.1 --format '{{.Id}}'` with test reports for exact runtime identification.

To add an application, derive a local image from the runtime:

```dockerfile
FROM localhost/linux-computer-use:0.1.1
RUN pacman -Syu --noconfirm --needed firefox && pacman -Scc --noconfirm
```

```sh
podman build -t localhost/cul-firefox -f Containerfile.firefox .
CUL_IMAGE=localhost/cul-firefox CUL_NETWORK=host \
  CUL_WORKDIR="$HOME/Projects/agent-work" linux-computer-use mcp
```

Applications in the temporary home do not retain profiles across sessions. Save deliverables to `/work`; design any additional persistent mounts deliberately in a custom wrapper.

## CLI in a container

MCP is the simplest transport because one container owns the entire stdio session. For a CLI controller reached by separate container invocations, put its socket inside the shared work directory, with the required directory permissions:

```sh
mkdir -m 700 "$PWD/agent-session"
# First process, left running:
CUL_WORKDIR="$PWD" linux-computer-use serve \
  --socket /work/agent-session/control.sock --mode isolated
# Another process, same CUL_WORKDIR and same UID:
CUL_WORKDIR="$PWD" linux-computer-use call get_state \
  --socket /work/agent-session/control.sock --image-out /work/agent-session/state.jpg
CUL_WORKDIR="$PWD" linux-computer-use call stop \
  --socket /work/agent-session/control.sock
```

This shared socket path is intentionally accessible to other processes with your UID. Native CLI sessions can use a private directory returned by `mktemp -d` instead.

## Current desktop

Use a native runtime and start MCP from the real graphical login session. Call `start_session` with `{"mode":"current"}`. The environment must contain the correct Wayland/X11 display and session bus. A sandboxed harness may require access to those sockets.

KDE/GNOME use the RemoteDesktop portal where provided. Grant the intended screen and input devices in the desktop’s consent UI. A denial or unsupported interface ends the request; the runtime does not bypass the portal or substitute an XWayland-only input path. Hyprland/Sway use advertised compositor protocols. X11 uses XTEST.

The portal test in this repository grants consent only inside a test-owned private desktop, with an explicit opt-in test flag. It is not a production auto-consent mechanism.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| `gi` or a typelib is missing | Use system Python and the native package recipe; pip alone cannot supply the desktop libraries |
| Native dependencies missing on SteamOS/Atomic | Use existing dependencies or rootless Podman; do not unlock the OS image |
| Podman reports subordinate UID/GID or user-namespace errors | Complete your distro’s rootless Podman setup; the installer does not rewrite account mappings |
| Package not found | Check your release’s repositories; keep Arch fully updated; include the dry-run report in an issue |
| No current-desktop portal | Check that the compositor provides RemoteDesktop, not only screenshots; use private mode where appropriate |
| Wayland app has no AT-SPI tree | Use observed screenshot pixels; custom canvases may not expose semantic controls |
| Text goes to another app | Observe and focus the intended target; use a new private instance/profile for single-instance apps |
| Hyprland private startup fails | Use a compatible KWin parent and GPU render node, or choose Sway for software rendering |
| Coordinates are wrong after a monitor change | Restart the session; pass the observed `frame_id` without pre-scaling coordinates |
| Container cannot access a deliverable | Save inside `/work`, and inspect `CUL_WORKDIR`; the container has its own temporary home |

For a useful bug report include distro, DE/version, session type, native/container mode, selected compositor, `doctor`, the installer’s `--dry-run` and a reproducible test. Review screenshots and logs before sharing them.
