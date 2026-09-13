"""MCP -> native Qt dialog and XWayland xterm, verified from app outputs."""
import base64
import json
import pathlib
import sys
import time
from e2e import Client, ROOT, wait_state

output = ROOT / "artifacts/native-apps"
output.mkdir(parents=True, exist_ok=True)
client = Client(output)
report = {"status": "failed", "checks": []}
try:
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    session = client.tool("start_session", {"compositor": "kwin"})
    directory = pathlib.Path(session["private_directory"])
    report["private_directory"] = str(directory)
    app = client.tool("launch_app", {"argv": ["kdialog", "--title", "CUL Qt E2E", "--inputbox", "Unicode Qt input"]})
    deadline = time.monotonic() + 8
    while True:
        windows = client.tool("list_windows")["windows"]
        window = next((w for w in windows if w["pid"] == app["pid"]), None)
        if window:
            break
        assert time.monotonic() < deadline, windows
        time.sleep(.1)
    client.tool("focus_window", {"window_id": window["id"]})
    phrase = "Qt natif — café 日本語 🐧"
    client.tool("type_text", {"text": phrase})
    picture = client.tool("screenshot", {"format": "png"})["image"]
    (output / "qt.png").write_bytes(base64.b64decode(picture["data"]))
    state = client.tool("get_state", {"pid": app["pid"], "screenshot": False})
    report["qt_accessibility"] = state["accessibility"]
    ok = next(n for n in state["accessibility"]["nodes"] if n["role"] in ("push button", "button") and n["name"] == "OK")
    client.tool("perform_action", {"token": ok["token"]})
    deadline = time.monotonic() + 5
    while not any(phrase in p.read_text() for p in directory.glob("app-*.log")):
        assert time.monotonic() < deadline, "Qt did not return the typed text"
        time.sleep(.05)
    report["checks"].append("Native Qt Wayland dialog: Unicode input, AT-SPI OK, exact returned text")
    terminal = output / "terminal.json"
    terminal.unlink(missing_ok=True)
    app = client.tool("launch_app", {"argv": ["xterm", "-u8", "-fa", "DejaVu Sans Mono", "-fs", "12",
        "-xrm", "XTerm*VT100.translations: #override Ctrl Shift <Key>V: insert-selection(CLIPBOARD)",
        "-T", "CUL XWayland E2E", "-e", sys.executable,
        str(ROOT / "tests/terminal_fixture.py"), str(terminal)]})
    deadline = time.monotonic() + 8
    while True:
        windows = client.tool("list_windows")["windows"]
        window = next((w for w in windows if w["title"] == "CUL XWayland E2E"), None)
        if window:
            break
        assert time.monotonic() < deadline, windows
        time.sleep(.1)
    client.tool("focus_window", {"window_id": window["id"]})
    time.sleep(.2)
    picture = client.tool("screenshot", {"format": "png"})["image"]
    (output / "xwayland-before.png").write_bytes(base64.b64decode(picture["data"]))
    phrase = "XWayland — français 日本語 🐧"
    client.tool("type_text", {"text": phrase, "paste_key": "Ctrl+Shift+v"})
    client.tool("press_key", {"key": "Return"})
    wait_state(terminal, lambda s: s["received"] == phrase)
    picture = client.tool("screenshot", {"format": "png"})["image"]
    (output / "xwayland.png").write_bytes(base64.b64decode(picture["data"]))
    report["checks"].append("XWayland xterm: clipboard bridge, keyboard Enter, exact Unicode consumed on stdin")
    client.close()
    assert client.process.returncode == 0, client.process.returncode
    report["status"] = "passed"
finally:
    client.close()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
