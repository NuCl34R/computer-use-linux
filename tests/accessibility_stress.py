"""Exercise AT-SPI cache lifetime while native applications appear/disappear."""
import json
import os
import signal
import sys
import time
from e2e import Client, ROOT, wait_state

output = ROOT / "artifacts/accessibility-stress"
output.mkdir(parents=True, exist_ok=True)
client = Client(output)
report = {"status": "failed", "application_lifecycles": 0, "snapshots": 0}
try:
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    client.tool("start_session", {"compositor": "kwin"})
    for index in range(20):
        fixture = output / f"fixture-{index}.json"
        fixture.unlink(missing_ok=True)
        app = client.tool("launch_app", {"argv": [sys.executable, str(ROOT / "tests/fixture.py"), str(fixture)]})
        wait_state(fixture, lambda s: s["ready"])
        deadline = time.monotonic() + 5
        while True:
            state = client.tool("get_state", {"pid": app["pid"], "screenshot": False})
            report["snapshots"] += 1
            entry = next((n for n in state.get("accessibility", {}).get("nodes", []) if n["name"] == "Unicode test entry"), None)
            if entry:
                break
            assert time.monotonic() < deadline, state
            time.sleep(.05)
        client.tool("set_value", {"token": entry["token"], "value": f"AT-SPI lifetime {index} ✓"})
        wait_state(fixture, lambda s: s["text"] == f"AT-SPI lifetime {index} ✓")
        # Exactly the fixture PID just launched by this test, no name matching.
        os.kill(app["pid"], signal.SIGTERM)
        for _ in range(3):
            client.tool("get_state", {"screenshot": False})
            report["snapshots"] += 1
        report["application_lifecycles"] += 1
    client.close()
    assert client.process.returncode == 0, client.process.returncode
    report["status"] = "passed"
finally:
    client.close()
    (output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
