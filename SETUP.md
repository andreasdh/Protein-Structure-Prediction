# Setting up on Fox

Follow this once. It takes about half an hour, most of it waiting for a
download. At the end you can run structure predictions and collect the results.

Everything here assumes you have an Educloud Research account and belong to a
project. If you do not, that comes first.

---

## 1. Log in

```bash
ssh <your-educloud-username>@fox.educloud.no
```

Your Educloud username is not your ordinary UiO username. It usually starts
with `ec-`. You are asked for a two-factor code first, then your password.

If it fails three times, stop and check the username. The server locks you out
for a while after repeated failures, and further attempts extend the lockout.

Find your project code and check your storage:

```bash
projects
dusage
```

Write the project code down. It goes into every job you submit.

In `dusage`, look at two things: how much space you have left, and the inode
percentage on the right. Inodes count files, not bytes. A filesystem can refuse
to create a file while reporting plenty of free space, and the error will say
"disk quota exceeded" either way.

---

## 2. Get the code

```bash
cd ~
git clone https://github.com/andreasdh/Protein-Structure-Prediction.git
cd Protein-Structure-Prediction
```

---

## 3. Get the container

The container holds Boltz-2 and everything it depends on, frozen at known
versions. You do not install anything.

Pull it **from a login node**, not from inside a job. It is a download, it
needs no GPU, and a job's time limit can expire mid-transfer.

```bash
mkdir -p /fp/projects01/<your-project>/$USER/images

apptainer pull /fp/projects01/<your-project>/$USER/images/boltz.sif \
    docker://ghcr.io/andreasdh/protein-structure-prediction/boltz:latest

apptainer cache clean -f
```

The image is about 6 GB and the download takes a few minutes. `cache clean`
afterwards matters: the cache holds a second full copy of everything, and it
has no value once the `.sif` file exists.

Tell the tools where it is:

```bash
echo 'export BOLTZ_IMAGE=/fp/projects01/<your-project>/$USER/images/boltz.sif' >> ~/.bashrc
echo 'export BOLTZ_CACHE=$HOME/boltz_cache' >> ~/.bashrc
source ~/.bashrc
mkdir -p $BOLTZ_CACHE
```

`BOLTZ_CACHE` is where model weights are stored, separately from the image.

---

## 4. Check it works

No GPU needed for this:

```bash
apptainer exec $BOLTZ_IMAGE boltz --help
apptainer exec $BOLTZ_IMAGE cat /opt/versions.txt | tail -3
```

The first should print a usage message. The second tells you the Python and
PyTorch versions inside. Those lines belong in a methods section.

---

## 5. Download the model weights

Once, from a login node, so that jobs do not all race for the same files:

```bash
mkdir -p ~/first_run && cd ~/first_run
cat > test.yaml <<'EOF'
version: 1
sequences:
  - protein:
      id: A
      sequence: <paste a sequence of 200 to 400 residues>
EOF

apptainer exec --bind $BOLTZ_CACHE:/opt/boltz_cache $BOLTZ_IMAGE \
    boltz predict test.yaml --out_dir weights_warmup --use_msa_server
```

Choose a well-studied protein whose structure has been known for years. Several
gigabytes of weights download first. Once the prediction itself starts, you can
interrupt with Ctrl-C; login nodes have no GPU and would run it on CPU.

Confirm:

```bash
ls -lh $BOLTZ_CACHE
```

---

## 6. Run something on a GPU

Check which nodes have room:

```bash
sinfo -p accel -o "%20n %10G %10t %15C"
```

Then ask for one:

```bash
srun --account=<your-project> --partition=accel --gpus=1 \
     --mem=32G --time=00:30:00 --pty bash
```

`--mem` is required on Fox; the job is rejected without it. `--time` is a hard
limit and the job is killed when it expires.

Inside the job:

```bash
cd ~/first_run
export BOLTZ_IMAGE=/fp/projects01/<your-project>/$USER/images/boltz.sif
time apptainer exec --nv --bind $BOLTZ_CACHE:/opt/boltz_cache $BOLTZ_IMAGE \
    boltz predict test.yaml --out_dir gpu_out --seed 1 \
    --diffusion_samples 1 --use_msa_server
```

`--nv` exposes the host GPU drivers to the container. Without it the program
sees no GPU and fails with an unhelpful message.

Record the time it took. It tells you what a larger run will cost.

---

## 7. Daily workflow

Use the wrapper rather than typing Apptainer commands. It finds the image
automatically and prints which environment it used:

```bash
./boltz_run.sh predict inputs/something.yaml --out_dir results/ --seed 1
```

Generate inputs and jobs from the configuration:

```bash
python3 make_inputs.py --config config.yaml --out inputs/ --constructs full
python3 make_slurm.py --config config.yaml --inputs inputs/
sbatch run_boltz.sh
squeue -u $USER
```

Collect and compare:

```bash
python3 collect.py --results results/ --out summary.csv --config config.yaml
python3 compare.py --summary summary.csv --reference reference/9s11.cif \
    --config config.yaml --calibration calib.csv --out comparison.csv
```

Use interactive sessions for debugging only. For anything that takes more than
a few minutes, submit with `sbatch` and walk away.

---

## 8. Before anything that gets reported

Three habits, and they are not optional.

**Commit first.** No production run with uncommitted changes. Then record what
produced the results:

```bash
git status            # must be clean
sbatch run_boltz.sh
./provenance.sh results/ "sbatch run_boltz.sh"
```

That writes `results/PROVENANCE.txt` with the commit hash, software versions,
GPU model and input checksums.

**Keep a logbook.** One entry in `notes/logbook.md` per run: what you ran, what
you saw, what you concluded, what you are doing next. Include the runs that
failed and the ideas you abandoned. The tools record what happened; only you
can record why.

**Run controls.** A positive control with a long-known structure, so a failure
tells you whether the pipeline or the model is at fault. Multiple seeds, because
one prediction is not a prediction.

---

## 9. When something fails

| Symptom | Cause |
| --- | --- |
| `No supported gpu backend found` | You are on a login node, not a GPU node |
| `Memory required by task is not available` | `--mem` missing, or you are already inside a job |
| The program sees no GPU | `--nv` missing from the `apptainer exec` call |
| `Disk quota exceeded` with space apparently free | Out of inodes. Check `dusage` |
| `srun: task 0: Killed` | The job hit its `--time` limit |
| `command not found: tmux` on a compute node | Start tmux on the login node, before `srun` |
| Connection drops mid-run | Use tmux. Reattach with `tmux attach` |
| Waiting a long time for resources | All GPUs busy. `Ctrl-C`, check `sinfo`, ask for a less contended type such as `--gpus=l40s:1` |
| `CUDA out of memory` | Ask for a larger card: `--gpus=a100_80:1` or an H100 or H200 |

Try the obvious fix twice. If it still fails, write down exactly what you ran
and the exact error, and ask. Do not spend a day on an infrastructure problem.

---

## What is where

| Path | Contents |
| --- | --- |
| `config.yaml` | sequences, domain boundaries, constructs, run settings |
| `make_inputs.py` | configuration to Boltz input files |
| `make_slurm.py` | input files to a job array |
| `collect.py` | Boltz output to one table of confidence values |
| `compare.py` | accuracy against a reference, and agreement between seeds |
| `provenance.sh` | records what produced a set of results |
| `boltz_run.sh` | runs Boltz through the container, or a local environment |
| `container/` | how the image is built, and the general container guide |
| `notes/logbook.md` | what was run and why |
