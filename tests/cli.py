"""Real Unix-socket CLI transport, file arguments, image export and shutdown."""
import json
import pathlib
import subprocess
import sys
import tempfile
import time
from e2e import ROOT, wait_state

entry = ROOT / "skills/linux-computer-use/scripts/cul.py"
output = ROOT / "artifacts/cli"
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="cul-cli-") as runtime:
    socket = pathlib.Path(runtime) / "control.sock"
    with open(output / "server.log", "w") as log:
        server = subprocess.Popen([sys.executable, str(entry), "serve", "--socket", str(socket)],
            stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            ready = json.loads(server.stdout.readline())
            assert ready["ready"] and (socket.stat().st_mode & 0o777) == 0o600
            def call(tool, arguments=None, image=None):
                payload = output / "arguments.json"
                payload.write_text(json.dumps(arguments or {}, ensure_ascii=False))
                command = [sys.executable, str(entry), "call", tool, "--socket", str(socket), "--json-file", str(payload)]
                if image:
                    command += ["--image-out", str(image)]
                result = subprocess.run(command, capture_output=True, text=True, timeout=20)
                assert result.returncode == 0, result.stderr + result.stdout
                answer = json.loads(result.stdout)
                assert answer["ok"], answer
                return answer["result"]
            fixture = output / "fixture.json"
            fixture.unlink(missing_ok=True)
            app = call("launch_app", {"argv": [sys.executable, str(ROOT / "tests/fixture.py"), str(fixture)]})
            wait_state(fixture, lambda s: s["ready"])
            time.sleep(.3)
            text = "CLI — café 'quoted' $(literal) `literal`"
            call("type_text", {"text": text})
            wait_state(fixture, lambda s: s["text"] == text)
            result = call("get_state", {"pid": app["pid"]}, output / "state.jpg")
            assert "data" not in result["image"] and (output / "state.jpg").read_bytes().startswith(b"\xff\xd8")
            call("stop")
            server.wait(timeout=10)
            assert server.returncode == 0 and not socket.exists()
            report = {"status": "passed", "checks": ["Unix socket mode 0600", "CLI JSON-file literal Unicode input",
                "Observed application callback", "Image exported and base64 omitted", "Stop removes socket and exits cleanly"]}
            (output / "report.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))
        finally:
            if server.poll() is None:
                server.terminate()
                server.wait(timeout=15)
