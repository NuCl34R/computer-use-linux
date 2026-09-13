# Contributing

[← Repository home](README.md)

Linux Computer Use is a preview implementation. Contributions should improve a real desktop workflow, protocol boundary or installation experience. Keep the native runtime self-contained under `skills/linux-computer-use/scripts/`; every harness must receive the same behavior.

## Development

Use your distribution’s Python 3.10+ and native libraries. `./install.sh --dry-run` describes the host; `--install-deps` can provision a supported mutable system. A pip-only virtual environment is not enough for GI, GTK, GStreamer and AT-SPI.

```sh
python3 tests/contracts.py
python3 tests/installer.py
python3 scripts/check_docs.py
python3 -m compileall -q scripts skills tests
python3 tests/e2e.py --compositor sway --output artifacts/dev-sway
```

Choose an installed private compositor (`kwin`, `sway`, `hyprland`, `xvfb`). Tests launch owned private desktops; they should never depend on clicking the contributor’s physical desktop. The portal consent test has a separate explicit opt-in flag and checks ownership before acting.

## Meaningful validation

For input, capture or accessibility changes, drive a real application through MCP and check application callbacks as well as changed screenshots. A mocked backend, successful process startup or screenshot-only probe does not establish desktop control. Lifecycle changes should run `tests/lifecycle.py`; accessibility changes should run `tests/accessibility_stress.py` too. These host scripts use private KWin.

Installer changes should cover distribution routing, immutable precedence, custom paths, update rollback and standalone installation. Desktop support claims must identify versions and distinguish private, container and current-desktop tests. Record the container image digest when applicable.

GitHub Actions runs private Sway and Xvfb E2E on Ubuntu 24.04. Local KWin, portal, nested Hyprland, multimonitor and atomic-host results are additional evidence, not implicitly covered by CI. Raw logs and screenshots go in ignored `artifacts/`; publish only reviewed, relevant evidence.

## Design constraints

- Prefer a documented native protocol over simulated XWayland coverage of Wayland apps.
- Keep input, coordinate scaling and observation contracts consistent across MCP and CLI.
- Keep portal consent and host/private desktop boundaries explicit.
- Do not introduce a root input service, telemetry, model SDK or network listener as a default dependency.
- Keep UI work on the appropriate GLib/AT-SPI thread and preserve guardian cleanup.
- Treat app text, titles, accessibility content and screenshots as untrusted input.

Open a focused pull request with the concrete problem, resulting behavior and relevant validation. Changes are accepted under the repository’s MIT license. No formal CLA is required by this repository.

## Packaging

```sh
python3 scripts/package.py --output dist
(cd dist && sha256sum -c SHA256SUMS)
```

The archive contains the installer, skill, tests and docs selected from tracked source files; it excludes Git metadata, raw local artifacts and environment files. It includes a per-file checksum manifest. The script checks that the archive can be read and its manifest verified. An archive should be extracted into a new directory and installed there before announcing a release.

Public release remains a maintainer decision after Omarchy workstation validation. Creating a tag or release must not change repository visibility.
