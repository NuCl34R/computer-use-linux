# Watch a private desktop

From a terminal on your normal desktop:

```sh
linux-computer-use watch
```

The viewer opens in your default browser. Choose an active session and display.
You can keep using your own desktop while observing the agent's independent
screen. The viewer also discovers sessions started by other MCP or CLI harnesses
under the same Linux user.

```sh
linux-computer-use watch --list
linux-computer-use watch --session SESSION_ID
linux-computer-use watch --no-open
```

The last command prints a local URL for opening in a browser of your choice.
The viewer requires system Python 3.10+ and a browser; it uses no host GTK or
PipeWire Python bindings. A container installation invokes the viewer on the
host and exposes only that container's spectator socket directory.

## What the controls do

- **Pause view** pauses image updates. The agent continues working.
- **Full screen** enlarges the view; the user's input still stays in the viewer.
- **Stop session** asks for confirmation, cancels active input and ends the
  controller. Applications owned by its private desktop close, so unsaved work
  can be lost. Existing applications in current-desktop mode are not terminated.
- Closing the tab or stopping the viewer process leaves agent sessions running.

The view refreshes at up to five frames per second while visible. It is an
observation view, not a remote keyboard or mouse. Screen pixels may include
private application content. Nothing is recorded or uploaded by this viewer.

## Local access

Controllers expose a mode-0600 Unix socket in an owner-only directory. Both ends
check the peer's Linux UID. The spectator accepts only status, preview and stop;
it cannot launch applications or inject input. Native controllers normally use
`$XDG_RUNTIME_DIR/linux-computer-use/watch`, with a private temporary-directory
fallback. `CUL_WATCH_DIR` selects another private directory and must agree
between the host controller and viewer. Container wrappers map a dedicated
subdirectory into the container automatically.

Only `watch` opens an HTTP listener, bound to `127.0.0.1` on an automatically
chosen port. API requests need a random token and are checked for Host and
Origin. The token is delivered in the URL fragment and retained in that tab's
session storage; it is not sent to external services. Keep the URL private.
There is no listener on the LAN. Same-user local processes remain within the
same trust boundary as the controller.

Set `CUL_WATCH=0` when starting a controller to disable spectator discovery.
Restart older running controllers after upgrading to gain the viewer. A stale
socket from a crashed controller is ignored when no server answers; normal
shutdown removes the socket. The small registry directories can remain.

## Capture and agent performance

Viewer reads do not create or evict the agent's screenshot frame IDs. Wayland
screencopy and X11 use independent connections; PipeWire uses its existing
immutable sample. Capture continues during actions such as dragging, with no
extra input seat or clipboard. Image encoding adds work only when a visible,
unpaused viewer requests frames. A busy or disconnected session is shown as
such; the last image is not proof that the agent is still running.
