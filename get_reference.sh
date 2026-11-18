#!/usr/bin/env bash
#
# Fetch the reference structure, extract the sequence from it, and check that
# config.yaml describes what actually arrived.
#
#     ./get_reference.sh            # 9S11, the CCTX2 cryo-EM structure
#     ./get_reference.sh 2LYZ       # anything else, by PDB id
#
# Run it from a login node: compute nodes may not reach the PDB.
#
# The sequence is taken from the structure rather than from a separate FASTA,
# so what gets predicted is what it will be scored against. Those two
# disagreeing is one of the easier ways to produce a confidently wrong
# comparison: the numbering lines up, the scores come out poor, and nothing
# says why.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PDB_ID="${1:-9S11}"
lower="$(echo "$PDB_ID" | tr '[:upper:]' '[:lower:]')"

cd "$HERE" || exit 1
mkdir -p reference sequences

REF="reference/${lower}.cif"

if [ -f "$REF" ]; then
    echo "   ok    $REF already here, not re-downloading"
    echo "         delete it first if you want a fresh copy"
else
    url="https://files.rcsb.org/download/${PDB_ID}.cif"
    echo "   downloading $url"
    # To a temporary name, so a failed transfer cannot leave a truncated file
    # that later parses as an empty structure.
    if curl -sSf --max-time 300 -o "$REF.partial" "$url"; then
        mv -f "$REF.partial" "$REF"
        echo "   ok    $REF ($(du -h "$REF" | cut -f1))"
    else
        rm -f "$REF.partial"
        echo "   FAIL  could not download $url" >&2
        echo "         Login nodes have internet; compute nodes may not." >&2
        echo "         Check the PDB id, or fetch it in a browser and put it" >&2
        echo "         at $REF" >&2
        exit 1
    fi
fi

echo
echo "   sequence"
CHAIN="$(python3 - <<'PY'
import sys
try:
    import yaml
    print(yaml.safe_load(open("config.yaml")).get("target", {})
          .get("reference_chain") or "")
except Exception:
    print("")
PY
)"
chain_arg=()
[ -n "$CHAIN" ] && chain_arg=(--chain "$CHAIN")

FASTA="sequences/${lower}.seq.txt"
python3 "$HERE/seq_from_structure.py" "$REF" "${chain_arg[@]}" \
    --yaml "inputs_${lower}.yaml" | tee "$FASTA"
rc=$?
[ $rc -ne 0 ] && exit $rc

echo
echo "   checking config.yaml against what arrived"
python3 "$HERE/check_reference.py" --config config.yaml --reference "$REF"
rc=$?

echo
if [ $rc -eq 0 ]; then
    echo "   Next: ./check_tools.sh, then"
    echo "         sbatch --account=<project> gpu_check.slurm"
else
    echo "   Fix config.yaml before running anything you intend to report."
fi
exit $rc
