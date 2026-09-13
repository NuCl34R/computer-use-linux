"""Boundary tests independent of an available display."""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/linux-computer-use/scripts"))
from cul.engine import Engine, TOOLS
from cul.server import validate, serve
from cul.keys import keycodes, keysym


class Contracts(unittest.TestCase):
    def test_schema_rejects_unknown_missing_types_and_nonfinite(self):
        for value in ({"secret": True}, {"x": True, "y": 1}, {"x": float("nan"), "y": 1}):
            with self.assertRaises(ValueError):
                validate(value, TOOLS["move"][1])
        with self.assertRaises(ValueError):
            validate({}, TOOLS["type_text"][1])

    def test_no_action_before_session(self):
        with self.assertRaisesRegex(RuntimeError, "No active session"):
            Engine().dispatch("click", {"x": 2, "y": 2})

    def test_relative_motion_validates_before_backend(self):
        engine = Engine()
        for dx, dy in [(True, 0), (float('inf'), 0), (0, float('nan')), (10001, 0)]:
            with self.assertRaises(ValueError):
                engine.move_relative(dx, dy)
        with self.assertRaises(ValueError):
            validate({'dx': 1, 'dy': 2, 'frame_id': 'not-a-coordinate'}, TOOLS['move_relative'][1])

    def test_stop_is_idempotent_but_session_cannot_restart(self):
        engine = Engine()
        self.assertTrue(engine.dispatch("stop")["stopped"])
        self.assertTrue(engine.dispatch("stop")["stopped"])
        with self.assertRaisesRegex(RuntimeError, "restart"):
            engine.start()

    def test_socket_rejects_shared_directory_before_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            pathlib.Path(directory).chmod(0o755)
            with self.assertRaises(PermissionError):
                serve(str(pathlib.Path(directory) / "control.sock"), {})

    def test_key_chords_and_unicode(self):
        self.assertEqual(keycodes("Ctrl+Shift+v"), [29, 42, 47])
        self.assertEqual(keycodes("Ctrl+plus"), [29, 42, 13])
        self.assertEqual(keycodes("CTRL+A"), keycodes("Ctrl+a"))
        self.assertEqual(keysym("🐧"), 0x0101f427)
        with self.assertRaises(ValueError):
            keycodes("Ctrl++")

    def test_mcp_framing_errors_do_not_kill_server(self):
        request = ["invalid\n", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"}}) + "\n",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"]
        process = subprocess.run([sys.executable, str(ROOT / "skills/linux-computer-use/scripts/cul.py"), "mcp"],
            input="".join(request), capture_output=True, text=True, timeout=10)
        answers = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("error", answers[0])
        self.assertEqual(len(answers[2]["result"]["tools"]), 20)


if __name__ == "__main__":
    unittest.main()
