# Git workflow

The aim is that every number in the write-up can be traced back to the exact
code and software versions that produced it.

## What goes where

Code and configuration in git. Results outside it.

```
Protein-Structure-Prediction/   <- the git repository
  config.yaml                   <- tracked
  *.py                          <- tracked
  *.sh                          <- tracked
  SETUP.md, GIT_WORKFLOW.md     <- tracked
  container/                    <- tracked
  sequences/                    <- tracked (small text files)
  inputs/                       <- tracked (generated, but small and decisive)
  notes/logbook.md              <- tracked
  reference/                    <- NOT tracked, downloaded by script
  results/                      <- NOT tracked
```

Results are too large and too numerous for git. The link between a results
directory and a commit runs through a `PROVENANCE.txt` file instead, written
by `provenance.sh`.

`.gitignore`:

```
results/
probe/
reference/*.cif
reference/*.pdb
__pycache__/
*.pyc
_selftest/
```

Input files are small and define exactly what was predicted, and
`provenance.sh` takes checksums of them, so keeping them in git lets a
checksum be tied to a commit. Leave `inputs/` out of `.gitignore`.

## Setting up on Fox

Git is already installed. To read the repository you need nothing:

```bash
cd ~
git clone https://github.com/andreasdh/Protein-Structure-Prediction.git
```

To push you need an SSH key. First check whether you already have one:

```bash
ls -l ~/.ssh/*.pub 2>/dev/null || echo "no key yet"
```

**If a key exists but under some other name**, it may be for something else,
and git will not find it on its own. Test whether GitHub already knows it:

```bash
ssh -T -i ~/.ssh/<that-key> git@github.com
```

`Hi <username>!` means you are done. Otherwise make a key for GitHub:

```bash
ssh-keygen -t ed25519 -C "fox-github-$USER" -f ~/.ssh/id_github -N ""
cat ~/.ssh/id_github.pub
```

**Paste the output of that `cat`**, the line beginning `ssh-ed25519
AAAAC3Nza...`, into GitHub under Settings, SSH and GPG keys, New SSH key.
Not the `SHA256:...` fingerprint that `ssh-keygen` prints: that is a hash for
recognising the key, not the key.

`-N ""` makes a key with no passphrase. That is right on a cluster, because a
queued job cannot answer a passphrase prompt. The cost is that anyone who can
read your home directory can use it, which on Fox is you and root.

Because the key is not at a default filename, git will not offer it unless
told:

```bash
cat >> ~/.ssh/config <<'EOF'

Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_github
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
```

`IdentitiesOnly yes` is the line that makes this work. Without it ssh offers
every key it can find, and GitHub can reject you after the first wrong one
rather than trying the rest. If `~/.ssh/config` already had content, check
there is a blank line before the new block.

Test, then clone over SSH:

```bash
ssh -T git@github.com     # expect: Hi <username>!
```

Then:

```bash
cd ~
git clone git@github.com:andreasdh/Protein-Structure-Prediction.git
cd Protein-Structure-Prediction
git config user.name "<your name>"
git config user.email "<your email>"
```

Compute nodes on Fox reach the internet through a proxy, so `git pull` works
there too. Push from a login node.

## The workflow

Edit locally, or on Fox through VS Code with the Remote-SSH extension. Then:

```bash
# on Fox, before a run
git pull
git status            # must be clean

# run
sbatch run_boltz.sh

# after the run
./provenance.sh results/ "sbatch run_boltz.sh"
```

**The rule: no production run with uncommitted changes.** `provenance.sh`
warns you if the working tree is dirty and embeds the full diff in the file,
so the run can still be reconstructed, but that is a warning rather than a
licence.

## The logbook

`notes/logbook.md` is what actually makes the work transparent. The tools
capture what ran, and the logbook captures why.

One entry per run:

```markdown
## 2026-11-15 — first calibration run, full construct

Commit: a3f91c2
Image: ghcr.io/.../boltz:v2, sha256:0a87df8c...
Ran: 5 seeds x 5 samples, full construct
Output: results/2026-11-15-full/

Observation: <what you actually saw, with numbers>

Interpretation: <what you think it means, and what else it could mean>

Decision: <what you are doing next, and why>
```

Write the entry before starting the next thing, while you still remember.
Include the runs that failed and the hypotheses you abandoned. A reader needs
to see the reasoning, not a tidied-up version of it.

## Reproducing a run

```bash
git log --oneline notes/logbook.md      # find the entry
git checkout <commit>                    # get the code as it was
cat results/<directory>/PROVENANCE.txt   # software versions and environment
```

## For the write-up

`PROVENANCE.txt` already contains what a methods section needs: the Boltz
version, the training cutoff note, the GPU, Python and package versions, and
checksums of the input files. Record the commit hash alongside each figure.
