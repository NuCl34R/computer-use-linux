#!/bin/sh
set -eu
cul_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cul_image=${CUL_IMAGE:-localhost/linux-computer-use:0.2.0}
if [ "${1:-}" = build ]; then
    podman build --network=host -t "$cul_image" -f "$cul_source/Containerfile" "$cul_source" >&2
    set -- prepare
fi
if [ "${1:-}" = prepare ]; then
    # Materialize this user's image mapping before a harness starts its MCP
    # timeout. This can be slow on the first run after an image rebuild.
    echo "Preparing rootless runtime for the current user..." >&2
    podman run --rm --read-only --security-opt label=disable --network=none \
      --userns=keep-id --user "$(id -u):$(id -g)" \
      --tmpfs /tmp:rw,exec,mode=1777 --tmpfs /run:rw,mode=755 \
      "$cul_image" python3 /opt/cul/cul.py tools </dev/null >/dev/null
    echo "Rootless runtime ready." >&2
    exit 0
fi
if [ "${1:-}" = watch ]; then
    # The viewer runs on the host using only Python's standard library.
    exec python3 "$cul_source/cul.py" "$@"
fi
cul_workdir=${CUL_WORKDIR:-$PWD}
cul_workdir=$(CDPATH= cd -- "$cul_workdir" && pwd)
if ! podman image exists "$cul_image" </dev/null; then
    echo "Build the runtime first: $0 build" >&2
    exit 1
fi
cul_watch_dir=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from cul.watch import container_directory; print(container_directory())' "$cul_source" </dev/null)
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
      --env CUL_WATCH="${CUL_WATCH:-1}" --env CUL_WATCH_DIR=/run/cul-watch --env CUL_WATCH_TRANSPORT=container \
      --env CUL_DEBUG_TRACE="${CUL_DEBUG_TRACE:-0}" \
      -v "$cul_watch_dir:/run/cul-watch:rw" \
      --userns=keep-id --user "$(id -u):$(id -g)" --device "$cul_device" \
      --tmpfs /tmp:rw,exec,mode=1777 --tmpfs /run:rw,mode=755 \
      -v "$cul_workdir:/work:rw" --workdir /work "$cul_image" python3 /opt/cul/cul.py "$@"
fi
exec podman run --rm -i --read-only --security-opt label=disable --network="${CUL_NETWORK:-none}" \
  --env CUL_WATCH="${CUL_WATCH:-1}" --env CUL_WATCH_DIR=/run/cul-watch --env CUL_WATCH_TRANSPORT=container \
  --env CUL_DEBUG_TRACE="${CUL_DEBUG_TRACE:-0}" \
  -v "$cul_watch_dir:/run/cul-watch:rw" \
  --userns=keep-id --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,exec,mode=1777 --tmpfs /run:rw,mode=755 \
  -v "$cul_workdir:/work:rw" --workdir /work "$cul_image" python3 /opt/cul/cul.py "$@"
