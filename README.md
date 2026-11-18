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

`selftest.py` builds a fake output tree and runs all four, so you can see the
tables before you have any real results. Run it first.

## Setup

```bash
pip install numpy pandas pyyaml
python3 selftest.py          # verify the pipeline works
```

RMSD and CA-lDDT are computed in the scripts themselves, so they work with
nothing else installed. Two further tools live in containers: **US-align**, for
TM-score, is in the Boltz image at `/usr/local/bin/USalign`, and
**OpenStructure**, the reference implementation of lDDT, is its own image.

**Run `compare.py` outside any container.** Apptainer cannot be run inside
Apptainer, so from inside the Boltz image OpenStructure is unreachable. Run it
outside, point two variables at the images, and it reaches into each as needed:

```bash
export BOLTZ_IMAGE=/projects/ecXXX/boltz.sif   # carries US-align
export OST_IMAGE=/projects/ecXXX/ost.sif       # carries OpenStructure
```

Anything missing is reported rather than silently dropped, and the startup
lines say which route each tool was found by. Keep models and reference under
`$HOME` or the working directory, since those are what Apptainer binds by
default.

See the lDDT section below for why both lDDT implementations are reported and
which number belongs in the report.

## Checking the tools work

`selftest.py` checks the scripts. `check_tools.sh` checks the whole chain,
including US-align and OpenStructure in their containers:

```bash
./check_tools.sh                      # downloads lysozyme (2LYZ)
./check_tools.sh reference/9s11.cif   # or use a structure you already have
```

It compares a structure against itself, so the right answer is known exactly
and any deviation is a bug rather than something to interpret. It also compares
it against a rotated copy, where superposition must undo the motion, and
against a copy roughened by a known amount of noise, where the expected RMSD is
known. It exits non-zero if any check fails, and reports which tools it could
not find rather than quietly leaving their columns empty.

Run it after any change to the environment: a new image, a new OpenStructure
version, a move to a different cluster.

## Before the first real run

Three things in `config.yaml` are placeholders and will give wrong answers if
left as they are.

**Domain boundaries.** The ranges under `domains:` are invented. Read the real
ones off Figure 1A and the methods of Schmieder et al. EMBO J 2026, and check
them against the residue numbering actually used in 9S11, which may not match
UniProt A8NDT7.

**Ligand SMILES.** The LacNAc and LacdiNAc entries say `PLACEHOLDER`. Fill them
in and check them. Pick the negative control from a glycan the published array
shows as non-binding.

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
#    OpenStructure is used automatically if $OST_IMAGE is set; see below
python3 compare.py --summary summary.csv --reference reference/9s11.cif \
    --config config.yaml --calibration calib.csv --out comparison.csv
python3 compare.py --summary summary.csv --consistency --out consistency.csv
```

## lDDT: two implementations

`compare.py` always computes lDDT itself, on CA atoms, so it works with nothing
installed. It additionally calls **OpenStructure**, the reference
implementation, whenever that is available, and reports both.

OpenStructure is distributed as a container. Pull it once from a login node and
point an environment variable at it:

```bash
cd /projects/ecXXX
apptainer pull ost.sif \
    docker://registry.scicore.unibas.ch/schwede/openstructure:latest
export OST_IMAGE=/projects/ecXXX/ost.sif     # or pass --ost-image
```

`compare.py` then adds these columns, and `lddt_source` records which
implementations ran:

| Column | What it is |
|---|---|
| `lddt_global` | CA-lDDT, computed in this script |
| `lddt_ost_bb` | OpenStructure `--bb-lddt`, **CA only for peptides**, so the same quantity |
| `lddt_ost_aa` | OpenStructure all-atom lDDT |
| `lddt_ost_bb_kept` | the backbone value averaged over the residues left after `exclude_from_comparison` |
| `lddt_ost_<domain>` | the same, per domain |
| `ost_clashes`, `ost_bad_bonds`, `ost_bad_angles` | stereochemistry problems in the model |

Three things follow from this:

- `lddt_global` and `lddt_ost_bb` measure the same thing, so they should agree.
  The script prints the mean difference across models. A systematic gap means
  one of the two is parameterised differently, and the reference implementation
  wins.
- The gap between `lddt_ost_bb` and `lddt_ost_aa` is side chains. Against a
  3.16 Å reference those are not reliably resolved, so part of that gap is our
  own uncertainty rather than the model's error. This is why the backbone
  number is the one to report, and why it has to be called CA-lDDT or backbone
  lDDT rather than just lDDT.
- With stereochecks on, which is the default and what CASP does, lDDT drops
  residues that fail them. A large `ost_bad_bonds` count therefore means the
  score covers fewer residues than the sequence length suggests. Use
  `--ost-no-stereochecks` when comparing the two implementations, since the CA
  version here filters nothing, and leave it off for numbers going in the
  report.

OpenStructure has no residue-exclusion flag, so `lddt_ost_bb_kept` is the mean
of its per-residue values over the retained residues. That is not identical to
a global lDDT recomputed with those residues deleted, because global lDDT pools
all distance pairs rather than averaging per-residue scores. For exact
comparability, trim both structures first and rerun.

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
