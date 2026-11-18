#!/usr/bin/env bash
#
# End-to-end test of the GPU half: predict a protein whose structure has been
# known for decades, then score it with the same scripts we will use on CCTX2.
#
#     sbatch --account=ec12 gpu_check.slurm                 # lysozyme
#     sbatch --account=ec12 gpu_check.slurm my.yaml ref.cif # anything else
#
# Submit it and walk away. Do NOT use an interactive srun for this: the queue
# for a GPU can be long, and an interactive session you are not watching when
# it finally starts is a session you lose.
#
# Watch it with:
#     squeue -u $USER
#     squeue -u $USER -o "%.18i %.9P %.20j %.8T %.10M %R"   # %R says why it waits
#     tail -f logs/gpu_check-<jobid>.out
#
#SBATCH --job-name=gpu_check
#SBATCH --partition=accel
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=logs/gpu_check-%j.out

set -uo pipefail

REPO="${SLURM_SUBMIT_DIR:-$PWD}"
WORK="${TOOLCHECK_DIR:-$HOME/toolcheck}"
INPUT="${1:-}"
REF="${2:-$WORK/ref.cif}"

echo "node      $(hostname)"
echo "job       ${SLURM_JOB_ID:-none}"
echo "started   $(date -u +%FT%TZ)"
echo

cd "$REPO" || exit 1
# shellcheck disable=SC1091
source env.sh || exit 1
echo

nvidia-smi --query-gpu=name,memory.total --format=csv,noheader \
    || echo "WARNING: no GPU visible. Was --gpus=1 accepted?"
echo

mkdir -p "$WORK"
cd "$WORK" || exit 1

if [ ! -f "$REF" ]; then
    echo "No reference at $REF. Run ./check_tools.sh first, which downloads"
    echo "one, or pass a reference as the second argument."
    exit 1
fi

# Build the input from the reference itself, so what is predicted is exactly
# what it will be scored against.
if [ -z "$INPUT" ]; then
    INPUT="$WORK/gpu_check.yaml"
    echo "building the input from $REF"
    python3 "$REPO/seq_from_structure.py" "$REF" --yaml "$INPUT" || exit 1
    echo
fi

OUT="$WORK/gpu_check_results"
rm -rf "$OUT"

echo "predicting $INPUT"
"$REPO/boltz_run.sh" predict "$INPUT" --out_dir "$OUT" \
    --use_msa_server --diffusion_samples 1
rc=$?
echo
if [ $rc -ne 0 ]; then
    echo "Boltz exited $rc. The output above says why. Common causes:"
    echo "  - the MSA server could not be reached from a compute node"
    echo "  - the weights are not in \$BOLTZ_CACHE; download them from a"
    echo "    login node first, because every array task would otherwise race"
    echo "  - not enough GPU memory for the sequence length"
    exit $rc
fi

echo "collecting"
python3 "$REPO/collect.py" --results "$OUT" --out "$WORK/gpu_check.summary.csv" \
    || exit 1
echo
echo "comparing against $REF"
python3 "$REPO/compare.py" --summary "$WORK/gpu_check.summary.csv" \
    --reference "$REF" --calibration "$WORK/gpu_check.calib.csv" \
    --out "$WORK/gpu_check.comparison.csv" || exit 1

echo
echo "=== verdict ==="
python3 - "$WORK/gpu_check.comparison.csv" <<'PY'
import sys
import pandas as pd

d = pd.read_csv(sys.argv[1])
if d.empty:
    print("   FAIL  nothing was compared")
    raise SystemExit(1)

def best(col):
    if col not in d.columns:
        return None
    v = d[col].dropna()
    return float(v.max()) if len(v) else None

tm = best("tmscore")
ld = best("lddt_ost_bb") or best("lddt_global")
rm = d["rmsd_global"].dropna().min() if "rmsd_global" in d.columns else None

print(f"   TM-score  {tm}")
print(f"   lDDT      {ld}")
print(f"   RMSD      {rm}")
print()

# A well-studied protein with deep alignments is an easy target: current
# models routinely clear 0.9. So this is a blunt check on the setup, not a
# measurement of the model.
if tm is None:
    print("   US-align produced no TM-score, so this cannot be judged on the")
    print("   usual number. Check the lDDT instead and fix US-align.")
    raise SystemExit(1)
if tm > 0.9:
    print("   PASS. The whole chain works: prediction, collection, scoring.")
    raise SystemExit(0)

print("   FAIL. On a target this easy the setup is at fault, not the model.")
print("   Look at, in order:")
print("     - MSA depth. If the alignment search failed quietly, the model")
print("       is predicting from the sequence alone. The Boltz output above")
print("       reports how many sequences it found.")
print("     - a numbering offset between prediction and reference")
print("     - the wrong chain being compared")
print("     - a sequence that is not continuous, if seq_from_structure.py")
print("       warned about gaps")
raise SystemExit(1)
PY
rc=$?

echo
echo "finished  $(date -u +%FT%TZ)"
exit $rc
