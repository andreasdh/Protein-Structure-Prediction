# CCTX2 prediction toolkit

Wrapper scripts around Boltz-2 for the CCTX2 project: generate inputs, submit
to Fox, collect results, compare against the cryo-EM structure.

Four scripts, run in order:

```
make_inputs.py   construct/ligand matrix  ->  Boltz-2 YAML files
make_slurm.py    YAML files + seeds       ->  Slurm array script
collect.py       Boltz output tree        ->  summary.csv   (confidence)
compare.py       summary.csv + 9S11       ->  comparison.csv (accuracy)
```

`selftest.py` builds a fake output tree and runs all four scripts, so you can see the
tables before you have any real results.

## Setup

```bash
pip install numpy pandas pyyaml
python3 selftest.py          # verify that the pipeline works
```

Optional but recommended: put **US-align** on PATH for TM-score. US-align is a program developed by the Zhang lab. Without it the scripts still compute RMSD and CA-lDDT, which cover most of what you need.

For anything going into a project report, use **OpenStructure** for lDDT rather than the CA-only version implemented here.

## Before the first real run

Three things in `config.yaml` are placeholders and will give wrong answers if
left as they are.

**Domain boundaries.** The ranges under `domains:` are invented. Read the real
ones off Figure 1A and the methods of Schmieder et al. EMBO J 2026.

**Ligand SMILES.** The LacNAc and LacdiNAc entries say `PLACEHOLDER`. Fill them
in and check them.

**Slurm account and partition.** Run `projects` and `sinfo` on Fox rather than
trusting the defaults.

You also need to supply two files:

```
sequences/cctx2.fasta     the CCTX2 sequence
reference/9s11.cif        downloaded from the PDB
```

## Typical run

```bash
# 1. inputs
python3 make_inputs.py --config config.yaml --out inputs/
python3 make_inputs.py --config config.yaml --out inputs/ \
    --constructs full btf12 --ligands lacdinac negative_control

# 2. jobs
python3 make_slurm.py --config config.yaml --inputs inputs/
#    test ONE task interactively before this:
sbatch run_boltz.sh

# 3. collect
python3 collect.py --results results/ --out summary.csv --config config.yaml

# 4. compare
python3 compare.py --summary summary.csv --reference reference/9s11.cif \
    --config config.yaml --calibration calib.csv --out comparison.csv
python3 compare.py --summary summary.csv --consistency --out consistency.csv
```

## What each output answers

| File | Question |
|---|---|
| `summary.csv` | How confident is the model, per construct and per domain? |
| `comparison.csv` | How accurate is it against 9S11, globally and per domain? |
| `calib.csv` | Does confidence track accuracy, residue by residue? |
| `consistency.csv` | Do different seeds agree with each other? |

The distinction matters. `summary.csv` contains no accuracy information at all.
Confidence scores are the model's opinion of itself.

## The measurement the project is built around

`compare.py --calibration` prints, per domain, the fraction of residues that are
**confidently wrong**: pLDDT above 80 with lDDT below 0.6. The hypothesis is
that this fraction is near zero for the BTF domains and substantial for the DUF
domain, because the DUF fold was novel and absent from the training data.

If that comes out as predicted, it is a clean, quantified example of a
confidence score measuring familiarity rather than correctness. That is a result
worth writing up, not a failed prediction.

## The cleaved state

`duf_alone` has no experimental counterpart as a free domain, but the DUF domain
*is* present in 9S11 as part of the intact protein. So comparing the isolated
prediction against the DUF region of 9S11 is a real measurement: it asks whether
the domain changes when released from the BTF cradle. `compare.py --reference`
does this automatically via the `regions` offset in the config.

Read it carefully though. A large deviation could mean a genuine conformational
change, or it could mean the model simply predicts isolated domains worse. The
`full` construct is the control that separates those: if the DUF domain is also
poorly predicted in context, the isolated-state difference says little.

## Notes on Boltz-2 on Fox

Download the model weights **once from a login node** into `BOLTZ_CACHE` before
submitting an array. Otherwise every task races to fetch the same files.

`--use_msa_server` sends the sequence to ColabFold's external server. It is off
by default in the config. Two reasons to leave it off: compute nodes often
cannot reach it, and it is an external data transfer. For CCTX2 itself the
sequence is published so this is not sensitive, but check before doing the same
with anything unpublished from the Krengel lab.

Precompute MSAs on a login node and pass them in the YAML instead when you can.

## Experimental design

Keep the number of **conditions** small and the number of **replicates** per
condition adequate. Five seeds times five diffusion samples is 25 structures per
condition, which is enough to see the spread. Five conditions at that depth is
125 structures, which is a comprehensible experiment.

A few hundred models spread thinly over many one-off conditions cannot be
compared with each other and will not answer anything.

## Checks to run regardless

- **Training cutoff.** Confirm and write down the cutoff for every model you
  use, and that 9S11 (map released 2026-03-18) falls after it. The whole
  benchmark rests on this.
- **Positive control.** Predict a protein of similar size whose structure was
  released long before the cutoff. If that fails, the pipeline is broken, not
  the model.
- **Negative control.** For the sugar co-folding, run a glycan the array shows
  as non-binding and check the model treats it differently.
- **Do not look at 9S11** before running predictions and recording confidence.
