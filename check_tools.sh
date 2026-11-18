#!/usr/bin/env bash
#
# Check that every tool in the chain actually works, before trusting any
# number it produces.
#
#   ./check_tools.sh                  # downloads lysozyme (2LYZ) and uses it
#   ./check_tools.sh reference/9s11.cif
#
# The idea: compare a structure against ITSELF. The right answer is then
# known exactly, so anything other than a perfect score is a bug in the
# setup rather than something to interpret. Then compare it against a copy
# that has been moved and a copy that has been roughened, where the expected
# behaviour is also known.
#
# None of this needs a GPU. Stage 5 does, and is optional.
#
# RUN THIS OUTSIDE ANY CONTAINER. See compare.py's header for why.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export TOOLCHECK_SRC="$HERE"          # so the embedded Python can import compare
WORK="${TOOLCHECK_DIR:-$HOME/toolcheck}"
PDB_ID="${PDB_ID:-2LYZ}"
INPUT="${1:-}"

pass=0
fail=0
note() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '   ok    %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '   FAIL  %s\n' "$*"; fail=$((fail+1)); }
info() { printf '         %s\n' "$*"; }

mkdir -p "$WORK/results"
cd "$WORK" || exit 1


# --------------------------------------------------------------- 1. the tools

note "1. What is available"

python3 - <<'PY'
import importlib, sys
print(f"         python {sys.version.split()[0]}")
if sys.version_info[:2] < (3, 10):
    print(f"   warn  this is an old Python. The login node's system Python is "
          f"not\n         the environment Boltz was installed into. If you "
          f"have a venv,\n         activate it first: source "
          f"~/envs/<name>/bin/activate")
missing = []
for m in ("numpy", "pandas", "yaml"):
    try:
        mod = importlib.import_module(m)
        print(f"   ok    {m} {getattr(mod, '__version__', '')}")
    except ImportError:
        print(f"   FAIL  {m} missing")
        missing.append(m)
if missing:
    pkgs = " ".join("pyyaml" if m == "yaml" else m for m in missing)
    print(f"\n   Nothing below can run without these. Either activate the "
          f"environment\n   they are installed in, or: pip install "
          f"--break-system-packages {pkgs}")
    raise SystemExit(1)
PY
if [ $? -ne 0 ]; then
    exit 1
fi

for exe in apptainer singularity; do
    command -v "$exe" >/dev/null && ok "$exe: $(command -v "$exe")"
done

for var in BOLTZ_IMAGE OST_IMAGE; do
    path="${!var:-}"
    if [ -z "$path" ]; then
        info "\$$var not set"
    elif [ -f "$path" ]; then
        ok "\$$var -> $path"
    else
        bad "\$$var points at $path, which does not exist"
    fi
done

if [ -z "${BOLTZ_IMAGE:-}" ] && ! command -v USalign >/dev/null; then
    info "no US-align route, so TM-score will be skipped below"
fi
if [ -z "${OST_IMAGE:-}" ] && ! command -v ost >/dev/null; then
    info "no OpenStructure route, so only CA-lDDT will be reported below"
fi


# ----------------------------------------------------------- 2. the structure

note "2. A reference structure"

if [ -n "$INPUT" ]; then
    if [ -f "$INPUT" ]; then
        cp "$INPUT" ref.cif
        ok "using $INPUT"
    else
        bad "$INPUT not found"
        exit 1
    fi
elif [ -f ref.cif ]; then
    ok "ref.cif already here, reusing it"
else
    url="https://files.rcsb.org/download/${PDB_ID}.cif"
    if curl -sSf --max-time 60 -o ref.cif "$url"; then
        ok "downloaded $PDB_ID"
    else
        bad "could not download $url"
        info "login nodes have internet; compute nodes may not."
        info "Or fetch it yourself and pass the path as an argument."
        exit 1
    fi
fi


# -------------------------------------------------- 3. build the three copies

note "3. Three copies of it, with known answers"

rm -f results/identity_model_0.* results/rigid_model_0.* results/rough_model_0.*
python3 "$HERE/make_testcase.py" ref.cif --out results/ || exit 1

python3 - <<'PY' || exit 1
import glob, os, sys
import pandas as pd
sys.path.insert(0, os.environ["TOOLCHECK_SRC"])
from compare import read_ca

rows = []
for c in ("identity", "rigid", "rough"):
    hits = glob.glob(f"results/{c}_model_0.*")
    if not hits:
        print(f"   FAIL  {c} copy was not written")
        raise SystemExit(1)
    rows.append({"predictor": "test", "construct": c, "ligand": "none",
                 "seed": 1, "model": 0, "cif": hits[0]})
pd.DataFrame(rows).to_csv("summary.csv", index=False)

# The construct regions must start at 1 so compare.py's offset is 0: these
# copies already carry the reference's own residue numbering. The upper bound
# comes from read_ca rather than from the file directly, so it is whatever
# compare.py itself will see.
nums, xyz, _ = read_ca("ref.cif")
if xyz is None or len(xyz) < 20:
    print("   FAIL  compare.py cannot read CA atoms from ref.cif")
    raise SystemExit(1)
with open("config_test.yaml", "w") as fh:
    fh.write("target:\n  reference_chain: A\n")
    fh.write("constructs:\n")
    for c in ("identity", "rigid", "rough"):
        fh.write(f"  {c}:\n    regions: [[1, {int(nums.max())}]]\n")
print(f"   ok    summary.csv and config_test.yaml written; compare.py sees "
      f"{len(xyz)} CA atoms, residues {nums.min()}-{nums.max()}")
PY


# ----------------------------------------------------------- 4. run and judge

note "4. compare.py"

TOOLCHECK_SRC="$HERE" python3 "$HERE/compare.py" \
    --summary summary.csv --reference ref.cif --config config_test.yaml \
    --calibration calib_test.csv --out comparison_test.csv || exit 1

note "5. Verdict"

python3 - <<'PY'
import numpy as np
import pandas as pd

d = pd.read_csv("comparison_test.csv").set_index("construct")
ok = fail = 0


def check(label, cond, detail):
    global ok, fail
    if cond:
        print(f"   ok    {label}: {detail}")
        ok += 1
    else:
        print(f"   FAIL  {label}: {detail}")
        fail += 1


def val(construct, col):
    if construct not in d.index or col not in d.columns:
        return None
    v = d.loc[construct, col]
    return None if pd.isna(v) else float(v)


# A structure against itself. These are not approximate expectations.
r = val("identity", "rmsd_global")
check("identity RMSD", r is not None and r < 0.01, f"{r} (must be 0.00)")
l = val("identity", "lddt_global")
check("identity CA-lDDT", l is not None and l > 0.999, f"{l} (must be 1.0)")

t = val("identity", "tmscore")
if t is None:
    print("   skip  identity TM-score: US-align not available")
else:
    check("identity TM-score", t > 0.999, f"{t} (must be 1.0)")

for col, name in (("lddt_ost_bb", "OpenStructure backbone lDDT"),
                  ("lddt_ost_aa", "OpenStructure all-atom lDDT")):
    v = val("identity", col)
    if v is None:
        print(f"   skip  identity {name}: not available")
    else:
        check(f"identity {name}", v > 0.999, f"{v} (must be 1.0)")

# Rigid-body motion must be undone by superposition.
r = val("rigid", "rmsd_global")
check("rigid-body RMSD", r is not None and r < 0.01,
      f"{r} (must be 0.00; a large value means coordinates are being "
      f"compared without superposition)")
t = val("rigid", "tmscore")
if t is not None:
    check("rigid-body TM-score", t > 0.999, f"{t} (must be 1.0)")
l = val("rigid", "lddt_global")
check("rigid-body CA-lDDT", l is not None and l > 0.999,
      f"{l} (must be 1.0; lDDT is superposition-free, so motion cannot "
      f"affect it)")

# Known noise, so the answer is a range rather than a point.
r = val("rough", "rmsd_global")
check("roughened RMSD", r is not None and 1.2 < r < 2.2,
      f"{r} (expected near 1.7 for 1.0 A noise per coordinate)")
l = val("rough", "lddt_global")
check("roughened CA-lDDT", l is not None and 0.3 < l < 0.95,
      f"{l} (must sit below 1.0 but well above 0)")

# Does the calibration machinery recover a correlation that is there by
# construction? If not, the pLDDT column is not being read correctly.
try:
    c = pd.read_csv("calib_test.csv")
    g = c[c["construct"] == "rough"].dropna(subset=["plddt", "lddt"])
    rr = np.corrcoef(g["plddt"], g["lddt"])[0, 1] if len(g) > 2 else np.nan
    check("calibration recovers a built-in correlation",
          rr > 0.4, f"r = {rr:.3f} (pLDDT was written to track the real error)")
except Exception as e:
    print(f"   FAIL  calibration: {e}")
    fail += 1

ca, ost = val("identity", "lddt_global"), val("identity", "lddt_ost_bb")
if ca is not None and ost is not None:
    print(f"\n   CA-lDDT {ca} vs OpenStructure backbone lDDT {ost}. These "
          f"measure the\n   same thing, so on an identical pair both must "
          f"be 1.0.")

print(f"\n   {ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
PY
status=$?

note "6. Optional: the GPU path"

cat <<'TXT'
         Everything above tests the analysis chain. To test prediction too,
         run one short job and feed its output through the same scripts:

           srun --account=ecXXX --partition=accel --gpus=1 --mem=32G \
                --cpus-per-task=8 --time=00:40:00 --pty bash
           # then, inside the job:
           ./boltz_run.sh inputs/<something_small>.yaml results/

           python3 collect.py --results results/ --out summary.csv
           python3 compare.py --summary summary.csv --reference ref.cif \
               --config config.yaml --calibration calib.csv --out comparison.csv

         Use a short sequence first. A 130-residue protein takes a few
         minutes; CCTX2 at 787 residues takes considerably longer, so do not
         discover a typo on that one.
TXT

exit $status
