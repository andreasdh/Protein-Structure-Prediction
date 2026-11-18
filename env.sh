# Environment for this project on Fox. SOURCE it, do not run it:
#
#     source env.sh
#
# Order matters. A venv does not contain an interpreter, only a pointer to
# one, so the module must be loaded first. Activating the venv without the
# module gives:
#
#     python3: error while loading shared libraries: libpython3.12.so.1.0
#
# Override any of these before sourcing if your layout differs:
#
#     PROJECT=ec12 VENV=$HOME/envs/boltz source env.sh

# Must be sourced: exports from a subshell are lost.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    echo "Run this as: source env.sh" >&2
    exit 1
fi

# Local overrides, for paths that differ between people and machines. Not
# tracked by git, so your layout does not become everyone's. Create it with
# whichever lines you need:
#
#     cat > env.local <<'EOF'
#     IMAGES=$HOME/images
#     VENV=$HOME/envs/boltz
#     EOF
#
# Anything already set in your shell still wins over this file, so a one-off
#     IMAGES=/somewhere/else source env.sh
# works for trying something without editing anything. That takes the four
# lines below: env.local is sourced as plain assignments, which would
# otherwise clobber what you just set.
_pre_PROJECT="${PROJECT:-}"
_pre_PYMODULE="${PYMODULE:-}"
_pre_VENV="${VENV:-}"
_pre_IMAGES="${IMAGES:-}"

if [ -f "env.local" ]; then
    # shellcheck disable=SC1091
    source "env.local"
    echo "env.sh: read env.local"
fi

[ -n "$_pre_PROJECT" ]  && PROJECT="$_pre_PROJECT"
[ -n "$_pre_PYMODULE" ] && PYMODULE="$_pre_PYMODULE"
[ -n "$_pre_VENV" ]     && VENV="$_pre_VENV"
[ -n "$_pre_IMAGES" ]   && IMAGES="$_pre_IMAGES"
unset _pre_PROJECT _pre_PYMODULE _pre_VENV _pre_IMAGES

PROJECT="${PROJECT:-ec12}"
PYMODULE="${PYMODULE:-Python/3.12.3-GCCcore-13.3.0}"
VENV="${VENV:-$HOME/envs/boltz}"

# Images default to the project area, but that is a shared allocation: on Fox
# the ec12 fileset has been down to 25 GB free with 99% of its inodes used,
# and a write there fails with "Disk quota exceeded" regardless of your own
# usage. Home has a personal quota, so it is the more predictable place.
# Put IMAGES=$HOME/images in env.local to use it.
IMAGES="${IMAGES:-/fp/projects01/$PROJECT/$USER/images}"

# The module that the venv was built against. If this ever stops matching,
# `cat $VENV/pyvenv.cfg` names the interpreter it actually wants.
module load "$PYMODULE" 2>/dev/null \
    || echo "env.sh: could not load $PYMODULE. 'module avail Python' lists what is here." >&2

_venv_ok=no
if [ -f "$VENV/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$VENV/bin/activate" && _venv_ok=yes
else
    echo "env.sh: no venv at $VENV. Create one with:" >&2
    echo "    python3 -m venv $VENV" >&2
    echo "    source $VENV/bin/activate" >&2
    echo "    pip install boltz numpy pandas pyyaml gemmi" >&2
fi

# Weights live in home rather than the project area: they unpack into many
# small files, and the project area ran out of inodes, not space.
export BOLTZ_CACHE="${BOLTZ_CACHE:-$HOME/boltz_cache}"

# compare.py reaches into these images for US-align and OpenStructure, which
# is why the analysis runs OUTSIDE any container. Apptainer cannot be run
# inside Apptainer.
export BOLTZ_IMAGE="$IMAGES/boltz.sif"
export OST_IMAGE="$IMAGES/ost.sif"

# ~/bin first, so a locally built tool wins over the image. Useful when the
# image is behind: US-align is one source file, and building it locally is
# faster than rebuilding and re-pulling several GB.
if [ -d "$HOME/bin" ]; then
    case ":$PATH:" in
        *":$HOME/bin:"*) ;;
        *) export PATH="$HOME/bin:$PATH" ;;
    esac
fi

if [ "$_venv_ok" = yes ]; then
    echo "python     $(python3 --version 2>&1 | awk '{print $2}')  ($VENV)"
else
    echo "python     $(python3 --version 2>&1 | awk '{print $2}')  NO VENV ACTIVE"
fi
unset _venv_ok
for v in BOLTZ_CACHE BOLTZ_IMAGE OST_IMAGE; do
    p="${!v}"
    if [ -e "$p" ]; then
        printf '%-10s %s\n' "$v" "$p"
    else
        printf '%-10s %s   MISSING\n' "$v" "$p"
    fi
done
echo
echo "Next: ./check_tools.sh"
