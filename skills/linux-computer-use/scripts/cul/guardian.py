"""Reclaim only registered private process groups if the controller dies.

The pipe is the lease: its EOF also covers SIGKILL/native crashes, which Python
finally handlers cannot catch. No host-wide process matching or input access.
"""
import json
import os
import pathlib
import signal
import sys
import time


def birth(pid):
    try:
        return pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def reclaim(groups):
    def send(pid, start, sig):
        now = birth(pid)
        # A still-existing group reserves its id even when its leader exited.
        # If the pid was reused by another leader, its start time differs.
        if now is None or now == start:
            try:
                os.killpg(pid, sig)
            except ProcessLookupError:
                pass
    for pid, start in reversed(groups):
        send(pid, start, signal.SIGTERM)
    time.sleep(.25)
    for pid, start in reversed(groups):
        send(pid, start, signal.SIGKILL)


if __name__ == "__main__":
    groups = []
    for line in sys.stdin:
        record = json.loads(line)
        groups.append((int(record["pid"]), str(record["start"])))
    reclaim(groups)
