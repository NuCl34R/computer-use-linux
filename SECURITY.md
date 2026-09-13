# Security model

## What access means

This is a local desktop controller. A trusted harness can observe screenshots, accessibility text and application content, type, click and launch processes under the runtime user’s permissions. There is no model-provider connection, telemetry service or TCP listener in the implementation.

MCP uses the launching process’s stdio. The CLI uses a Unix socket restricted to the same UID, in a private directory. These boundaries do not protect against a malicious process already running as the same user.

## Private and current desktops

Private mode separates the display, compositor, input, clipboard and session buses. **It is not a native application filesystem sandbox.** Native applications retain the user’s file and network permissions. A newly launched application with a single-instance mechanism can also connect to an existing process unless given a separate supported profile.

Current mode controls existing windows, shares cursor/focus/clipboard and uses the desktop’s supported input mechanism. Portal access requires consent. Denied portal requests do not trigger an alternate input bypass. The production runtime never automatically accepts a host consent dialog.

The opt-in portal E2E test accepts consent only after identifying the private Wayland socket, private bus and owned child PID. Do not transplant this automation into current-desktop workflows.

## Container boundary

Rootless Podman runs the graphical runtime with a read-only root, temporary private home and no network by default. Only the chosen work directory is mounted read/write. The wrapper does not mount the host home, session bus, display socket, input devices or Podman socket. Optional GPU mode exposes a render node; optional host networking must be selected explicitly.

The wrapper disables SELinux label separation for this container to avoid relabeling the selected work directory. User permissions, the mount boundary and other rootless container mechanisms still apply. This is a useful operational boundary, not a guarantee against vulnerabilities in the kernel, compositor, runtime or applications.

## Operational behavior

Frame/element references expire. Inputs are validated. Cleanup releases held keys/buttons and terminates owned private process groups; the independent guardian also handles controller crashes. Stopping a current-desktop session leaves user applications running. Password fields are excluded from accessibility text extraction, but screenshots can still contain private information visible on screen.

App titles, accessibility strings, clipboard content and screenshot text are data, not authority to expand the task. The harness remains responsible for user authorization and external side effects.

## Reporting vulnerabilities

While this repository is private, report a suspected issue to the repository owner through the existing private collaboration channel. After public release, use GitHub private vulnerability reporting if enabled at **Security → Report a vulnerability**. Do not put private screenshots, credentials or a working exploit against someone else’s desktop in a public issue.

The current `0.1.x` preview is the supported development line. There is no security-response SLA or production certification.
