#!/bin/sh
# A local, inspectable entry point. No network bootstrap or shell profile changes.
set -eu
cul_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cul_python=${CUL_PYTHON:-/usr/bin/python3}
if [ ! -x "$cul_python" ]; then
    cul_python=$(command -v python3 || true)
fi
if [ -z "$cul_python" ]; then
    echo 'System Python 3.10+ is required. Install it through your distribution, then rerun this script.' >&2
    exit 1
fi
exec "$cul_python" "$cul_root/scripts/install.py" "$@"
