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

## 3. Every session starts the same way

Once the setup below is done, this is the whole ritual:

```bash
cd ~/Protein-Structure-Prediction
source env.sh
```

`env.sh` loads the Python module, activates the virtual environment, and sets
the three variables the scripts look for. It prints what it found and says
`MISSING` for anything that is not there yet, so you can see the state of the
environment rather than guess at it.

Source it, do not run it. `./env.sh` would set the variables in a subshell
that exits immediately, leaving your own shell unchanged. The script refuses
to run that way rather than appearing to work.

Put the setup in `env.sh` rather than in `~/.bashrc`. A `.bashrc` is per
person and per machine, it is easy to have it loaded in some shells and not
others, and nothing records which version of the setup produced a given
result. `env.sh` is in git with the code.

If your paths differ, override them without editing the file:

```bash
PROJECT=ec12 VENV=$HOME/envs/boltz source env.sh
```

---

## 4. Get the container

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

`env.sh` already expects the image at that path, so there is nothing to export
by hand. Create the weight directory:

```bash
mkdir -p $HOME/boltz_cache
source env.sh          # should now print BOLTZ_IMAGE without MISSING
```

`BOLTZ_CACHE` is where model weights are stored, separately from the image. It
lives in your home directory rather than the project area: the weights unpack
into many small files, and the project area ran out of inodes, not space.

---

## 5. A Python environment for the analysis scripts

The container runs the predictions. The analysis scripts run **outside** it,
because `compare.py` reaches into both containers and Apptainer cannot be run
inside Apptainer. So they need their own Python with `numpy`, `pandas` and
`pyyaml`.

The system Python on the login node is 3.9 and does not have them. Use a
module plus a virtual environment:

```bash
module load Python/3.12.3-GCCcore-13.3.0
python3 -m venv ~/envs/boltz
source ~/envs/boltz/bin/activate
pip install --upgrade pip
pip install numpy pandas pyyaml gemmi
```

**The order matters, every time.** A virtual environment does not contain an
interpreter, only a pointer to one. Activate it without loading the module
first and you get:

```
python3: error while loading shared libraries: libpython3.12.so.1.0:
cannot open shared object file: No such file or directory
```

That is the symptom of a missing module, not a broken environment. `env.sh`
does both in the right order, which is why you should use it rather than
remembering this.

If the module name above ever stops working, `cat ~/envs/boltz/pyvenv.cfg`
names the exact interpreter the environment was built against, and
`module avail Python` lists what the cluster currently offers.

---

## 6. Get OpenStructure

`compare.py` computes lDDT itself, on CA atoms. OpenStructure is the reference
implementation, and running both is how we show our own version is right.
Pull it next to the Boltz image, from a login node:

```bash
cd /fp/projects01/<your-project>/$USER/images
apptainer pull ost.sif \
    docker://registry.scicore.unibas.ch/schwede/openstructure:latest
apptainer cache clean -f
```

One image is one inode, so this adds nothing to the inode pressure.

---

## 7. Check everything works

```bash
cd ~/Protein-Structure-Prediction
source env.sh
./check_tools.sh
```

This compares a structure against itself, so the right answer is known
exactly: RMSD must be 0.00 and every lDDT and TM-score must be 1.0000.
Anything else is a fault in the setup rather than something to interpret. It
then checks a rotated copy, where superposition must undo the motion, and a
copy roughened by a known amount of noise, where the expected RMSD is known.

It exits non-zero if any check fails, and reports which tools it could not
find instead of quietly leaving their columns empty. Run it again after any
change to the environment.

---

## 8. Check the container works

No GPU needed for this:

```bash
apptainer exec $BOLTZ_IMAGE boltz --help
apptainer exec $BOLTZ_IMAGE cat /opt/versions.txt | tail -3
```

The first should print a usage message. The second tells you the Python and
PyTorch versions inside. Those lines belong in a methods section.

---

## 9. Download the model weights

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

## 10. Run something on a GPU

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

## 11. Daily workflow

First, always:

```bash
cd ~/Protein-Structure-Prediction
source env.sh
```

Then use the wrapper rather than typing Apptainer commands. It finds the image
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

## 12. Before anything that gets reported

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

## 13. When something fails

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
