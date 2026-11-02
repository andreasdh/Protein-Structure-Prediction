#!/bin/bash
# Run Boltz, through the container when one is present and the pip
# environment otherwise. Every command below is identical either way:
#
#     ./boltz_run.sh predict inputs/full.yaml --out_dir results/ --seed 1
#
# Why this exists: you want to explore in a pip environment that is quick to
# change, and produce reportable results in a frozen container, WITHOUT the
# two being different software. This script makes the switch one setting
# rather than a different way of working, and prints which one it used so
# the logbook records it.
#
# Settings (override in the environment or in ~/.bashrc):
#   BOLTZ_IMAGE   path to the .sif file. If it exists, the container is used.
#   BOLTZ_VENV    path to the pip environment, used when no image is found.
#   BOLTZ_CACHE   model weights, bound into the container.

set -euo pipefail

BOLTZ_IMAGE="${BOLTZ_IMAGE:-$HOME/images/boltz.sif}"
BOLTZ_VENV="${BOLTZ_VENV:-$HOME/envs/boltz}"
BOLTZ_CACHE="${BOLTZ_CACHE:-$HOME/boltz_cache}"

mkdir -p "$BOLTZ_CACHE"

if [ -f "$BOLTZ_IMAGE" ]; then
    echo "[boltz_run] container: $BOLTZ_IMAGE" >&2
    # --nv exposes the host NVIDIA drivers. Without it the container sees no GPU.
    exec apptainer exec --nv \
        --bind "$BOLTZ_CACHE":/opt/boltz_cache \
        --env BOLTZ_CACHE=/opt/boltz_cache \
        "$BOLTZ_IMAGE" \
        boltz "$@"
else
    if [ ! -f "$BOLTZ_VENV/bin/activate" ]; then
        echo "[boltz_run] no container at $BOLTZ_IMAGE and no environment at $BOLTZ_VENV" >&2
        exit 1
    fi
    echo "[boltz_run] pip environment: $BOLTZ_VENV" >&2
    echo "[boltz_run] NOTE: not the frozen container. Fine for exploration;" >&2
    echo "[boltz_run]       production runs for the report should use the image." >&2
    # shellcheck disable=SC1091
    source "$BOLTZ_VENV/bin/activate"
    export BOLTZ_CACHE
    exec boltz "$@"
fi
