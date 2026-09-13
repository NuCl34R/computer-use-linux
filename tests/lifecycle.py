"""Real compositor recovery after EOF, SIGTERM, and an uncatchable SIGKILL."""
import json
import os
import pathlib
import signal
import sys
import time
from e2e import Client, ROOT

output = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "artifacts/lifecycle"
output.mkdir(parents=True, exist_ok=True)
report = {"status": "failed", "checks": []}
for end in ("eof", "term", "kill"):
    directory = output / end
    directory.mkdir(exist_ok=True)
    client = Client(directory)
    client.request("initialize", {"protocolVersion": "2025-06-18"})
    session = client.tool("start_session")
    root = pathlib.Path(session["private_directory"])
    pids = json.loads((root / "session.json").read_text())["pids"]
    app = client.tool("launch_app", {"argv": [sys.executable, "-c", "import time; time.sleep(120)"]})
    pids.append(app["pid"])
    if end == "eof":
        client.process.stdin.close()
    else:
        client.process.send_signal(signal.SIGTERM if end == "term" else signal.SIGKILL)
    client.process.wait(timeout=15)
    # Zombies have released all resources and input. They can briefly await
    # reaping by PID1 after the intentional SIGKILL, unlike normal stop.
    def running(pid):
        try:
            return pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            return False
    deadline = time.monotonic() + 5
    while any(running(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(.05)
    alive = [pid for pid in pids if running(pid)]
    assert not alive, (end, alive)
    if end != "kill":
        assert client.process.returncode == 0, (end, client.process.returncode)
    report["checks"].append({"termination": end, "owned_pids": pids, "remaining_running": alive})
    client.stderr.close()
report["status"] = "passed"
(output / "report.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
