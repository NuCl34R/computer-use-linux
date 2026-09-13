#!/bin/sh
set -eu
cul_repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$cul_repo/skills/linux-computer-use/scripts/container-run.sh" "$@"
