# Validation record

[← Repository home](../README.md) · [French detailed baseline](../skills/linux-computer-use/references/validation.md)

## Preview 0.1.1 · September 13, 2026

The [0.1.1 checks](preview-0.1.1-validation.json) cover 14 installer contracts,
seven input contracts, migration of the actual SteamOS skill backup, and real
application control through the installed native KWin and container Sway
launchers. Both launchers expose 20 tools, enter Unicode into a GTK field,
verify the resulting screenshot and stop. The first container initialization
attempt timed out; the subsequent full GUI run passed. The record retains that
observation. Earlier reports below remain historical baselines; physical workstation
coverage depends on the configurations actually tested.

## Relative pointer input · September 13, 2026

The `move_relative` addition passed the native GTK/MCP acceptance suite on
private KWin, rootless container Sway, rootless container Xvfb and a KDE portal
controlling an owned private KWin desktop. Each suite observed native motion
callbacks, exercised the existing input and capture tools, and confirmed cleanup
of owned processes. Seven input/transport contract tests and the installed native
launcher test also passed. No additional Hyprland run is claimed for this change.

The [structured runtime results](relative-pointer-validation.json) retain the
runtime hash, assertions and measurements. These runs used the same GTK fixture
and timing method as the baseline below; they did not control the physical desktop.

## Initial Linux baseline · September 13, 2026

All **13 required reports passed**. The [machine-readable record](../skills/linux-computer-use/references/validation-results.json) retains assertions, timings, runtime versions and the image ID. Raw local logs, bus identifiers and temporary process paths are kept in ignored `artifacts/`; reviewed GUI captures are included under [visual assets](assets/ASSETS.md).

The host was SteamOS **3.8.26**, KWin **6.4.3**, Python **3.13.5**. The container had Hyprland **0.56.2**, Sway **1.12**, KWin **6.7.5**, Python **3.14.7**, GStreamer **1.28.7** and PipeWire **1.6.8**.

| Report | Result and scope |
| --- | --- |
| `e2e-kwin` | Real native private KWin; complete MCP GUI interaction |
| `e2e-portal` | Real KDE portal in an owned private desktop with explicit consent |
| `e2e-hyprland` | Private Hyprland in the Arch runtime with GPU render access |
| `e2e-sway` | Software-rendered private Sway |
| `e2e-xvfb` | Private X11 using Xvfb |
| `native-apps` | Qt kdialog and XWayland xterm, exact Unicode results |
| `multimonitor` | Second Sway output at x=1280, 125% scale, reduced-image input mapping |
| `lifecycle` | EOF, SIGTERM and SIGKILL, guardian cleanup and no remaining active owned processes |
| `cli` | Same-UID Unix socket, JSON-file text, image output and shutdown |
| `installed-skill` | Full MCP suite using an independently copied skill |
| `container-sway` | Embedded distributed runtime; rootless, read-only root, no GPU or network |
| `container-xvfb` | Same distributed image, X11 path |
| `accessibility-stress` | 20 app lifecycles and 80 snapshots during changing AT-SPI trees |

Six transport/input contract tests also passed. The runtime hash is recorded in reports that ran after the final implementation was assembled.

## Universal installer validation

The installer adds executable tests for twelve distro scenarios, immutable precedence, Omarchy/Hyprland detection, complete-native selection on SteamOS, incomplete KWin fallback, safe os-release parsing, quoted paths, transactional rollback, update backups, manifest-protected removal, source-directory protection and the container launcher.

A fresh install into a separate directory on SteamOS passed the full native KWin E2E suite again: **2.08 ms** warm screenshot median and **52.85 ms** action-to-verified-image median in that run. These extra measurements are not substituted into the historical baseline.

Both actual installed launchers also passed the GTK/MCP acceptance test: native
KWin and rootless container Sway. See [installer results](installer-validation.json).

The [GitHub Actions workflow](../.github/workflows/ci.yml) installs system libraries on Ubuntu 24.04, runs the installer/contracts/docs checks, installs a standalone skill and drives real private Sway and Xvfb sessions through MCP. Workflow results are available under [Actions](https://github.com/NuCl34R/computer-use-linux/actions). The first corrected run passed on both Sway and Xvfb: [recorded CI evidence](ci-validation.json). CI does not substitute for testing a physical workstation’s compositor, GPU, scaling and applications.

## Measurement method

Thirty warm screenshots per backend, 1280×800, JPEG quality 85, local MCP round trip included. PipeWire reads/encodes the latest available frame, while wlroots/X11 request a new capture. These are different capture operations and should not be interpreted as identical workloads.

Five semantic actions per backend were confirmed by native application callbacks and followed by a changed post-action PNG. The action-to-verified-image result includes the 40 ms repaint allowance. PipeWire keepalive repeats do not count as changed frames. LLM inference, model networking and remote application delays are excluded. The small sample describes this run on this host, not a population of hardware.

**No macOS/Windows comparison was performed.** The project does not claim measured performance parity.

## Reproduce a native run

```sh
python3 tests/contracts.py
python3 tests/installer.py
python3 tests/e2e.py --compositor kwin --output artifacts/e2e-kwin
python3 tests/native_apps.py
python3 tests/cli.py
python3 tests/lifecycle.py
python3 tests/accessibility_stress.py
```

The last four host scripts use KWin. Choose `sway`, `hyprland` or `xvfb` for their respective generic E2E runs, with those dependencies installed. The optional portal test creates and verifies a private KWin/portal environment before granting consent:

```sh
python3 tests/e2e.py --compositor kwin --output artifacts/e2e-portal \
  --portal-private-consent
```

Do not use that test as a mechanism to accept a physical-desktop grant.

## Reproduce the container runtime

```sh
scripts/container-run.sh build
mkdir -p artifacts/container-sway
podman run --rm --read-only --network=none --userns=keep-id \
  --user "$(id -u):$(id -g)" --security-opt label=disable \
  --env CUL_TEST_ENTRY=/opt/cul/cul.py \
  --tmpfs /tmp:rw,exec,mode=1777 --tmpfs /run:rw,mode=755 \
  -v "$PWD:/work:ro" -v "$PWD/artifacts/container-sway:/results:rw" \
  --workdir /work localhost/linux-computer-use:0.1.1 \
  python3 tests/e2e.py --compositor sway --output /results
```

Use `xvfb` for X11. Nested Hyprland additionally needs a compatible parent and GPU render node, such as `--device /dev/dri/renderD128`. `tests/multimonitor.py /results` exercises two Sway outputs in the same environment.

## Installed launcher

After installing either runtime, this test invokes the **actual installed launcher**, initializes MCP, opens a private GTK app, enters Unicode, verifies a real callback and changed screenshot, and stops the session:

```sh
python3 tests/installed_launcher.py --launcher "$HOME/.local/bin/linux-computer-use" \
  --runtime native --output artifacts/launcher-native
# For a launcher configured by --runtime container:
python3 tests/installed_launcher.py --launcher "$HOME/.local/bin/linux-computer-use" \
  --runtime container --output artifacts/launcher-container
```

The container test shares only this repository as `/work`, runs with networking disabled and uses Sway. Use this test after a packaged installation too.

## Limits

The portal test did not control the physical desktop. GNOME, COSMIC, every atomic distribution and every compositor version are not individually tested. Private desktop portability relies on its own runtime; it is not uniform native support for every host window-manager API. Native window focus is implemented for KDE, Hyprland, Sway and X11. Screen topology is fixed at session start.

For a physical workstation, record the installer plan, `doctor` output,
compositor and GPU versions, display resolutions/scales and application versions.
Run the installed-launcher test above in a private desktop first and check that
your own input and clipboard remain usable independently. Test the current
desktop separately with a disposable application, verifying window discovery,
screenshot coordinates, Unicode input, scrolling, dragging and input release
after stopping. Record each mode as passed, failed or not tested.
