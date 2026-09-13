"""An actual terminal consumer, not a shell or a programmatic text edit."""
import json
import pathlib
import sys
import time

print("CUL XWayland: enter Unicode text", flush=True)
value = input()
pathlib.Path(sys.argv[1]).write_text(json.dumps({"received": value}, ensure_ascii=False))
print("Received:", value, flush=True)
time.sleep(60)
