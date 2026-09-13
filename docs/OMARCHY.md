# Omarchy workstation acceptance

[← Repository home](../README.md) · [Installation details](INSTALLATION.md)

**Status: awaiting a test on the intended Omarchy workstation.** Native Hyprland was exercised in a private Arch container; that does not certify your host compositor, GPU, installed applications or configuration. This guide turns the next test into reproducible evidence.

## 1. Install from the private repository

Use an authenticated GitHub client while the repository is private:

```sh
gh repo clone NuCl34R/computer-use-linux
cd computer-use-linux
./install.sh --dry-run
```

Expect the report to identify the Arch family, `omarchy: true`, and Hyprland/Wayland when run from the graphical session. Omarchy detection uses its version command or installation version file. A terminal launched outside the graphical session may correctly report `headless`.

On an up-to-date mutable Omarchy installation:

```sh
./install.sh --install-deps
~/.local/bin/linux-computer-use doctor
```

The native recipe installs the necessary libraries and Sway as a software-rendered private-desktop fallback. **The private compositor can differ from the host compositor.** You can keep using Hyprland while the agent works in private Sway. A complete KWin installation may be selected instead; `--dry-run` shows the decision.

For a rootless container with a fixed application runtime:

```sh
./install.sh --runtime container --build-container
```

Use `--update` when replacing an installation. There is no Omarchy config patch, plugin hook, keybinding or startup service in this release.

## 2. Validate independent work first

From the repository checkout, with native test dependencies installed:

```sh
mkdir -p artifacts/omarchy
./install.sh --dry-run > artifacts/omarchy/install-plan.json
~/.local/bin/linux-computer-use doctor > artifacts/omarchy/doctor.json
omarchy-version > artifacts/omarchy/omarchy-version.txt
Hyprland --version > artifacts/omarchy/hyprland-version.txt
python3 tests/contracts.py
python3 tests/installer.py
CUL_TEST_ENTRY="$HOME/.codex/skills/linux-computer-use/scripts/cul.py" \
  python3 tests/e2e.py --compositor sway --output artifacts/omarchy/sway
```

Adjust `CUL_TEST_ENTRY` if you chose another harness/path. That variable selects a native Python entry point; it does not invoke a container launcher. For the installed container route use [the launcher acceptance test](VALIDATION.md#installed-launcher).

While the private desktop test runs, type and move the pointer in your own desktop. Confirm your input and clipboard remain independent. The automated report checks the agent application’s callbacks, screenshots and cleanup; the human observation establishes that the host remained comfortable to use.

## 3. Exercise host Hyprland deliberately

This is a separate acceptance step. Connect the native MCP server from your Omarchy graphical session, then ask the agent to use `mode: "current"` on a disposable test application you open for that purpose. The current mode shares focus and clipboard with you.

Verify:

- Window discovery identifies the intended app and output.
- A screenshot shows the expected display, including scaling.
- A click and French/Japanese/emoji input reach the intended field.
- Scroll and drag work where the test app supports them.
- A fresh screenshot and the app’s visible state confirm the change.
- Stopping releases input without closing unrelated host applications.

Do not infer success from `$DISPLAY` or a screenshot alone. The runtime must use real Wayland protocols for Wayland apps.

## 4. Record the result

Save the commit (`git rev-parse HEAD`), installer report, Omarchy/Hyprland versions, GPU/driver, screen resolutions/scales, selected runtime and application versions. Attach the E2E `report.json` and reviewed captures to the private repository issue or discussion of the test.

Record **passed**, **failed**, or **not tested** separately for private Sway, private Hyprland (if attempted), host Hyprland, Unicode, fractional scaling and concurrent human use. The project can then update its matrix with precise evidence.

Making the repository public remains a separate maintainer action after this validation. A future integrated Omarchy edition can be considered from the observed behavior and user feedback.
