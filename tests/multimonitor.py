"""Two real Sway outputs with fractional scaling and nonzero global origin."""
import base64
import json
import os
import pathlib
import sys
import time
from e2e import Client, ROOT, wait_state, colour
sys.path.insert(0, str(ROOT / "skills/linux-computer-use/scripts"))
from cul.engine import Engine

output = pathlib.Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
outer = Engine()
client = None
report = {"status": "failed"}
try:
    outer.start(compositor="sway")
    ipc = outer.windowing._sway
    assert ipc(0, "create_output")[0]["success"]
    monitors = ipc(3)
    target = next(o["name"] for o in monitors if o["name"] != "HEADLESS-1")
    assert ipc(0, f"output {target} mode 1600x1200 scale 1.25 pos 1280 0")[0]["success"]
    assert ipc(0, f"workspace 2 output {target}")[0]["success"]
    assert ipc(0, "workspace 2")[0]["success"]
    fixture = output / "fixture.json"
    fixture.unlink(missing_ok=True)
    app = outer.launch_app([sys.executable, str(ROOT / "tests/fixture.py"), str(fixture)])
    wait_state(fixture, lambda s: s["ready"])
    time.sleep(.3)
    client = Client(output)
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    session = client.tool("start_session", {"mode": "current", "backend": "wlr"})
    report["displays"] = session["displays"]
    display = next(d for d in session["displays"] if d["name"] == target)
    assert display["x"] == 1280 and display["width"] == 1280, display
    image = client.tool("screenshot", {"display": display["id"], "max_width": 800, "format": "png"})["image"]
    assert image["capture_width"] == 1600 and image["width"] == 800 and image["scale_x"] == 1.6, image
    (output / "fractional.png").write_bytes(base64.b64decode(image["data"]))
    x, y = colour(image, (49, 95, 168))
    client.tool("click", {"x": x, "y": y, "frame_id": image["frame_id"]})
    wait_state(fixture, lambda s: s["clicks"] == 1)
    client.close()
    assert client.process.returncode == 0, client.process.returncode
    report.update(status="passed", checks=["Two real outputs enumerated", "Fractional physical/logical/image dimensions",
        "Screenshot pixel click reached the second output at global x=1280"])
finally:
    if client:
        client.close()
    outer.close()
    (output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
