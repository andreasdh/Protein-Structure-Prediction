#!/usr/bin/env python3
"""Write a Slurm array script covering every (input YAML, seed) pair.

Usage:
    python3 make_slurm.py --config config.yaml --inputs inputs/ --out run_boltz.sh
    sbatch run_boltz.sh

The job list is written to joblist.txt next to the script so you can read it,
edit it, or resubmit a subset with --array.
"""

import argparse
import glob
import os

import yaml

TEMPLATE = """#!/bin/bash
#SBATCH --job-name=boltz-cctx2
#SBATCH --account={account}
#SBATCH --partition={partition}
#SBATCH --gpus={gpus}
#SBATCH --cpus-per-task={cpus}
#SBATCH --mem={mem}
#SBATCH --time={time}
#SBATCH --array=1-{n}
#SBATCH --output=logs/%x-%A_%a.out
#SBATCH --error=logs/%x-%A_%a.err

# Fail loudly rather than producing half a result.
set -euo pipefail

# Verify these two before the first production run:
#   partition   ->  sinfo
#   account     ->  projects
# The defaults in config.yaml are guesses.

module --quiet purge
# Adjust to whatever your Boltz install needs. A container is usually cleaner:
#   apptainer exec --nv boltz.sif boltz predict ...
# module load Python/3.11
# source /projects/{account}/venvs/boltz/bin/activate

export BOLTZ_CACHE={cache}

# Download the weights ONCE from a login node before submitting, otherwise
# every array task races to fetch them into the same cache.
if [ ! -d "$BOLTZ_CACHE" ]; then
    echo "BOLTZ_CACHE $BOLTZ_CACHE does not exist. Run the weight download"
    echo "from a login node first." >&2
    exit 1
fi

JOB=$(sed -n "${{SLURM_ARRAY_TASK_ID}}p" {joblist})
YAML=$(echo "$JOB" | cut -f1)
SEED=$(echo "$JOB" | cut -f2)
STEM=$(basename "$YAML" .yaml)
OUT={outdir}/${{STEM}}/seed${{SEED}}

mkdir -p "$OUT"
echo "task ${{SLURM_ARRAY_TASK_ID}}: $YAML seed $SEED -> $OUT"

boltz predict "$YAML" \\
    --out_dir "$OUT" \\
    --seed "$SEED" \\
    --diffusion_samples {samples} \\
    --recycling_steps {recycling} \\
    --output_format mmcif \\
    --write_full_pae \\
    {msa_flag}

echo "done: $OUT"
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--inputs", default="inputs")
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--out", default="run_boltz.sh")
    ap.add_argument("--joblist", default="joblist.txt")
    args = ap.parse_args()

    with open(args.config) as fh:
        cfg = yaml.safe_load(fh)
    run = cfg["run"]
    sl = cfg["slurm"]

    yamls = sorted(glob.glob(os.path.join(args.inputs, "*.yaml")))
    if not yamls:
        raise SystemExit(f"No .yaml files in {args.inputs}/. Run make_inputs.py first.")

    seeds = list(range(1, int(run["seeds"]) + 1))
    jobs = [(y, s) for y in yamls for s in seeds]

    with open(args.joblist, "w") as fh:
        for y, s in jobs:
            fh.write(f"{y}\t{s}\n")

    msa_flag = "--use_msa_server" if run.get("use_msa_server") else \
               "# --use_msa_server   (disabled in config; see README)"

    script = TEMPLATE.format(
        account=sl["account"], partition=sl["partition"], gpus=sl["gpus"],
        cpus=sl["cpus_per_task"], mem=sl["mem"], time=sl["time"],
        n=len(jobs), cache=sl["boltz_cache"], joblist=args.joblist,
        outdir=args.outdir, samples=run["diffusion_samples"],
        recycling=run["recycling_steps"], msa_flag=msa_flag,
    )

    with open(args.out, "w") as fh:
        fh.write(script)
    os.chmod(args.out, 0o755)
    os.makedirs("logs", exist_ok=True)

    n_models = len(jobs) * int(run["diffusion_samples"])
    print(f"wrote {args.joblist}: {len(jobs)} tasks "
          f"({len(yamls)} inputs x {len(seeds)} seeds)")
    print(f"wrote {args.out}: array 1-{len(jobs)}")
    print(f"total structures expected: {n_models}")
    print("\nBefore sbatch:")
    print("  1. sinfo              -> confirm the partition name")
    print("  2. projects           -> confirm the account")
    print("  3. download weights from a login node into BOLTZ_CACHE")
    print("  4. test ONE task interactively before submitting the array")


if __name__ == "__main__":
    main()
