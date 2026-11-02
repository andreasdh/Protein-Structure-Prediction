#!/bin/bash
# Write a provenance record next to a set of results.
#
#   ./provenance.sh <output-directory> ["command that was run"]
#
# Call it from inside the git repo, right after a prediction finishes. It
# records what produced the results so a reader can tell, months later, which
# code and which model version a number came from.
#
# The point: results are too big for git, so they live outside it. This file
# is the thread tying a results directory back to a commit.

set -euo pipefail

OUT="${1:?usage: provenance.sh <output-directory> [command]}"
CMD="${2:-}"

mkdir -p "$OUT"
FILE="$OUT/PROVENANCE.txt"

{
    echo "# Provenance"
    echo "written          : $(date -Is)"
    echo "host             : $(hostname)"
    echo "user             : ${USER:-$(id -un)}"
    echo "output_dir       : $(readlink -f "$OUT")"
    [ -n "$CMD" ] && echo "command          : $CMD"
    echo

    echo "## Code"
    if git rev-parse --git-dir >/dev/null 2>&1; then
        echo "git_commit       : $(git rev-parse HEAD)"
        echo "git_branch       : $(git rev-parse --abbrev-ref HEAD)"
        echo "git_remote       : $(git config --get remote.origin.url 2>/dev/null || echo none)"
        if [ -n "$(git status --porcelain)" ]; then
            echo "git_clean        : NO - uncommitted changes present"
            echo "## Uncommitted changes at run time"
            git status --short | sed 's/^/    /'
            echo "## Diff"
            git diff | sed 's/^/    /'
        else
            echo "git_clean        : yes"
        fi
    else
        echo "git_commit       : NOT A GIT REPOSITORY"
        echo "  -> results cannot be traced to a code version"
    fi
    echo

    echo "## Software"
    echo "python           : $(python --version 2>&1 || echo unknown)"
    for pkg in boltz torch numpy pandas; do
        v=$(pip show "$pkg" 2>/dev/null | awk '/^Version:/{print $2}' || true)
        printf "%-17s: %s\n" "$pkg" "${v:-not installed}"
    done
    echo "cuequivariance   : $(python -c 'import cuequivariance_torch as m; print(getattr(m,"__version__","present"))' 2>/dev/null || echo 'not installed (kernels disabled)')"
    echo

    echo "## Hardware"
    if command -v nvidia-smi >/dev/null 2>&1; then
        (nvidia-smi --query-gpu=name,memory.total,driver_version \
                   --format=csv,noheader 2>/dev/null || true) \
            | sed 's/^/gpu              : /'
    else
        echo "gpu              : none visible"
    fi
    [ -n "${SLURM_JOB_ID:-}" ] && echo "slurm_job        : $SLURM_JOB_ID"
    echo

    echo "## Model provenance"
    echo "boltz_cache      : ${BOLTZ_CACHE:-unset}"
    if [ -n "${BOLTZ_CACHE:-}" ] && [ -d "${BOLTZ_CACHE}" ]; then
        find "$BOLTZ_CACHE" -maxdepth 1 -type f -printf "  %f  %s bytes  %TY-%Tm-%Td\n" 2>/dev/null
    fi
    echo
    echo "NOTE: Boltz-2 structural training uses PDB entries released up to"
    echo "2023-06-01. Confirm this for the exact version above, and record the"
    echo "release date of any reference structure used as ground truth."
    echo

    echo "## Inputs"
    for f in inputs/*.yaml config.yaml; do
        [ -f "$f" ] && printf "  %-28s sha256=%s\n" "$f" "$(sha256sum "$f" | cut -c1-16)"
    done
} > "$FILE"

echo "wrote $FILE"
if git rev-parse --git-dir >/dev/null 2>&1 && [ -n "$(git status --porcelain)" ]; then
    echo "WARNING: uncommitted changes. Commit before a production run so the" >&2
    echo "         results can be traced to an exact code version." >&2
fi
