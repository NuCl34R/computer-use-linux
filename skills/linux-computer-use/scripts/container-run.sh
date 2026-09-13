#!/bin/sh
set -eu
cul_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cul_image=${CUL_IMAGE:-localhost/linux-computer-use:0.1.1}
if [ "${1:-}" = build ]; then
    exec podman build --network=host -t "$cul_image" -f "$cul_source/Containerfile" "$cul_source"
fi
cul_workdir=${CUL_WORKDIR:-$PWD}
cul_workdir=$(CDPATH= cd -- "$cul_workdir" && pwd)
if ! podman image exists "$cul_image"; then
    echo "Build the runtime first: $0 build" >&2
    exit 1
fi
if [ "${CUL_GPU:-0}" = 1 ]; then
    # Render nodes provide GPU allocation, not access to keyboards or DRM seats.
    set -- --device /dev/dri/renderD128 "$@"
    cul_gpu_args=true
else
    cul_gpu_args=false
fi
# Keep GPU runtime arguments separate from the CLI argument vector.
if "$cul_gpu_args"; then
    cul_device=$2
    shift 2
    exec podman run --rm -i --read-only --security-opt label=disable --network="${CUL_NETWORK:-none}" \
      --userns=keep-id --user "$(id -u):$(id -g)" --device "$cul_device" \
      --tmpfs /tmp:rw,exec,mode=1777 --tmpfs /run:rw,mode=755 \
      -v "$cul_workdir:/work:rw" --workdir /work "$cul_image" python3 /opt/cul/cul.py "$@"
fi
exec podman run --rm -i --read-only --security-opt label=disable --network="${CUL_NETWORK:-none}" \
  --userns=keep-id --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,exec,mode=1777 --tmpfs /run:rw,mode=755 \
  -v "$cul_workdir:/work:rw" --workdir /work "$cul_image" python3 /opt/cul/cul.py "$@"
