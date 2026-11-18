#!/usr/bin/env bash
#
# Pull or refresh the two container images, safely.
#
#     source env.sh
#     ./pull_images.sh            # only what is missing
#     ./pull_images.sh --force    # refresh both, discarding the caches
#
# This exists because the obvious hand-typed version has a trap in it. With
# $BOLTZ_IMAGE unset:
#
#     apptainer pull --force $BOLTZ_IMAGE docker://...
#
# collapses to `apptainer pull --force docker://...`, which succeeds, invents
# its own filename, and drops a multi-gigabyte .sif in whatever directory you
# happened to be in. Nothing warns you. `set -u` below turns that silent
# mistake into an error before anything downloads.
#
# Pull from a LOGIN NODE. It is a download, it needs no GPU, and a job's time
# limit can expire mid-transfer.

set -euo pipefail

force=no
[ "${1:-}" = "--force" ] && force=yes

BOLTZ_REPO="docker://ghcr.io/andreasdh/protein-structure-prediction/boltz:latest"
OST_REPO="docker://registry.scicore.unibas.ch/schwede/openstructure:latest"

if [ -z "${BOLTZ_IMAGE:-}" ] || [ -z "${OST_IMAGE:-}" ]; then
    echo "BOLTZ_IMAGE or OST_IMAGE is not set. Run 'source env.sh' first." >&2
    exit 1
fi

mkdir -p "$(dirname "$BOLTZ_IMAGE")" "$(dirname "$OST_IMAGE")"

# apptainer pull reuses its own cache and reports "Using cached SIF image",
# which reads like a successful download. Clearing the cache first is what
# makes --force actually fetch.
if [ "$force" = yes ]; then
    echo "clearing the Apptainer cache so --force really refetches"
    apptainer cache clean -f
fi

pull () {
    local target="$1" source="$2" name="$3"
    if [ -f "$target" ] && [ "$force" = no ]; then
        echo "$name: already at $target, leaving it alone (--force to refresh)"
        return
    fi
    echo
    echo "$name: pulling into $target"
    # Into a temporary name first, so a failed or interrupted transfer cannot
    # leave a half-written image where a working one used to be.
    local tmp="$target.partial.$$"
    if apptainer pull --force "$tmp" "$source"; then
        mv -f "$tmp" "$target"
        echo "$name: done"
    else
        rm -f "$tmp"
        echo "$name: FAILED, the existing image at $target is untouched" >&2
        return 1
    fi
}

pull "$BOLTZ_IMAGE" "$BOLTZ_REPO" "boltz"
pull "$OST_IMAGE"   "$OST_REPO"   "openstructure"

apptainer cache clean -f    # the cache holds a second full copy, of no use now

echo
echo "what is in the Boltz image:"
apptainer exec "$BOLTZ_IMAGE" tail -3 /opt/versions.txt \
    || echo "   could not read /opt/versions.txt" >&2
for exe in boltz USalign foldseek; do
    if apptainer exec "$BOLTZ_IMAGE" which "$exe" >/dev/null 2>&1; then
        echo "   ok      $exe"
    else
        echo "   MISSING $exe   (the image predates it; rebuild and pull again)"
    fi
done

echo
echo "Next: ./check_tools.sh"
