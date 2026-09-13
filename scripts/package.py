#!/usr/bin/env python3
"""Build a checksummed, deterministic source archive from Git-tracked files."""
import argparse
import gzip
import hashlib
import io
import os
import pathlib
import subprocess
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PREFIX = "linux-computer-use-0.1.0"
EXCLUDED = {"artifacts", "dist", ".git", ".codex", ".agents", ".venv", "__pycache__"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "dist")
    args = parser.parse_args()
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    files = {}
    for name in sorted(filter(None, paths)):
        path = pathlib.PurePosixPath(name)
        if any(part in EXCLUDED for part in path.parts) or path.name.startswith(".env") or path.suffix in (".pyc", ".sock"):
            continue
        source = ROOT / path
        if source.is_symlink() or not source.is_file():
            raise RuntimeError("Source must be a regular tracked file: " + name)
        files[name] = (source.read_bytes(), 0o755 if os.access(source, os.X_OK) else 0o644)
    if not {"install.sh", "LICENSE", "README.md", "scripts/install.py", "skills/linux-computer-use/SKILL.md"} <= files.keys():
        raise RuntimeError("Required files are not tracked; add the intended source files to Git first")
    manifest = "".join(hashlib.sha256(data).hexdigest() + "  " + name + "\n" for name, (data, _) in files.items())
    files["MANIFEST.sha256"] = (manifest.encode(), 0o644)
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / (PREFIX + ".tar.gz")
    with destination.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w") as archive:
        for name, (data, mode) in sorted(files.items()):
            info = tarfile.TarInfo(PREFIX + "/" + name)
            info.size, info.mode, info.mtime = len(data), mode, 0
            archive.addfile(info, io.BytesIO(data))
    with tarfile.open(destination) as archive:
        for name, (data, _) in files.items():
            if archive.extractfile(PREFIX + "/" + name).read() != data:
                raise RuntimeError("Archive verification failed: " + name)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    (args.output / "SHA256SUMS").write_text(digest + "  " + destination.name + "\n")
    print(f"Verified {len(files)} archive entries: {destination}\nSHA-256 {digest}")


if __name__ == "__main__":
    main()
