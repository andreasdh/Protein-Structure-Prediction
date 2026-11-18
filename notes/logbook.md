# Logbook

One entry per run. The tools record what ran; this records why, and what we
concluded. Write the entry before starting the next thing, while you still
remember. Include the runs that failed and the hypotheses that were dropped:
a reader needs the reasoning, not a tidied-up version of it.

Template:

```markdown
## YYYY-MM-DD — short title

Commit:
Image:
Ran:
Output:

Observation: what was seen, with numbers.
Interpretation: what it may mean, and what else it could mean.
Decision: what happens next, and why.
```

---

## 2026-11-18 — setup validated end to end on lysozyme

Commit: 157df4e
Image: boltz.sif pulled 2026-11-18 from
ghcr.io/andreasdh/protein-structure-prediction/boltz:latest, carrying
Foldseek and US-align; ost.sif from
registry.scicore.unibas.ch/schwede/openstructure:latest
Ran: `sbatch --account=ec12 gpu_check.slurm`, Slurm job 4454895 on gpu-5.
One diffusion sample, MSA server enabled, sequence taken from the 2LYZ
mmCIF by `seq_from_structure.py`.
Output: `~/toolcheck/gpu_check_results/`, scored into
`gpu_check.comparison.csv`

**Observation.** 129 residues, 82 seconds, 11.2 minutes queueing. RMSD
0.35 Å, CA-lDDT 0.993, TM-score above 0.9. Mean pLDDT 98.25, confidence
0.978, pTM 0.962. Confidence calibration r = 0.715 across the 129 residues.
Peak memory 10.1 GiB of the 32 requested; CPU utilisation 14.8% across 8
cores.

Separately, `check_tools.sh` on the same reference: CA-lDDT and
OpenStructure's backbone lDDT differ by a mean of 0.0011, largest 0.0034.

**Interpretation.** The chain works: prediction, collection, scoring,
calibration. Nothing here says anything about the model's ability. Lysozyme
is in every training set, so 0.35 Å is what an easy target looks like and
the point of the run was the plumbing.

Two findings beyond that. Compute nodes do reach api.colabfold.com, so the
MSA path is available and the question about using it is about data rather
than network. And the agreement between the two lDDT implementations is the
evidence that the CA version in `compare.py` computes what OpenStructure's
`--bb-lddt` computes, which was asked about and had only an argument behind
it until now.

The calibration r of 0.715 should not be read as meaningful. Both pLDDT and
lDDT sit in a very narrow band on a target this easy, so the correlation is
computed over almost no range.

Resource use is a weak guide to CCTX2. Attention scales with the square of
the length and CCTX2 is six times longer, so 10.1 GiB does not extrapolate.

**Decision.** Run CCTX2 full-length next, as a single seed with one
diffusion sample first, to measure runtime and memory before committing to
5 seeds × 5 samples. Decide `use_msa_server` before that run rather than
after, because alignment depth is one of the variables under test and a run
without it is a different experiment.

Not done yet: `find_models_openfold3()` in `collect.py` is still a stub, and
the LacNAc and LacdiNAc SMILES in `config.yaml` are still placeholders.
