# Containers on an HPC cluster

A working recipe for building a software environment once and running it
anywhere. Written with Fox in mind, but nothing here is specific to Fox.

## Why

A container is a prepacked box holding a program and everything it needs:
libraries, Python version, system packages. The program sees its own
environment rather than the machine's, so it behaves the same everywhere.

It is not a virtual machine. A container shares the host kernel and carries
almost no performance overhead.

Three problems it solves:

**Dependencies.** Tools like AlphaFold, Boltz or OpenMM need particular
versions of PyTorch, CUDA and a dozen other packages that must agree with each
other. On a cluster you have no root and cannot install system packages.

**Reproducibility.** A pip or conda environment drifts. In two years
`pip install <tool>` gives a different version, against a different CUDA, with
different dependencies. A `.sif` file is frozen. That is the difference between
writing "we used version 2.2.1" and being able to hand over exactly what ran.

**Inodes.** A Python environment is tens of thousands of files. A container is
one. On a shared filesystem that has run out of file slots, this is not a
nicety.

When you do **not** need a container: the software is already available as a
module. Check `module spider <name>` first. A module is compiled against the
machine's own MPI and GPU setup and is often faster than a generic image.

## Apptainer, not Docker

HPC systems do not use Docker. The Docker daemon runs with root privileges,
which is unacceptable on a shared machine.

Apptainer runs in user space with no daemon, and your user identity is
preserved inside the container. It was formerly called Singularity, and the
`singularity` command often still exists as an alias. An image is a single
`.sif` file that can be copied with `scp`.

Apptainer pulls and converts Docker images, so that whole ecosystem is
available even though Docker is not.

Check that it is there:

```bash
apptainer --version
```

## Why you cannot build wherever you like

**Running** a container needs no special privileges.

**Building** one needs root or fakeroot. You have neither on a cluster.

**And Apptainer does not run on macOS.** So your Mac is not an option either.
You can install Docker Desktop and build a Docker image there, but then you
need Docker running locally and you upload several gigabytes over your own
connection.

The simplest route is to let a CI service build it. That needs root nowhere,
no local Docker, and it makes the build part of your version history.

## The workflow

```
Dockerfile in the repository
        |
        v
GitHub Actions builds on a tag or a button press
        |
        v
Image published to ghcr.io
        |
        v
apptainer pull on the cluster  ->  one .sif file
```

### 1. Dockerfile

Put it in `container/Dockerfile`. A minimal template for something GPU-based:

```dockerfile
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3-pip git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && python3 -m pip install --upgrade pip

# Pin the versions. That is the whole point of the image.
RUN python3 -m pip install --no-cache-dir \
        <package>==<version> \
        numpy pandas

# Record what actually landed inside.
RUN python3 -m pip list --format=freeze > /opt/versions.txt

WORKDIR /work
```

Two rules:

Pin versions explicitly. An unpinned version number makes the image as
ephemeral as the environment you were trying to replace.

Write a version list into the image. Then you can ask a finished image what it
contains without starting anything.

### 2. Workflow

`.github/workflows/container.yml`:

```yaml
name: build container

on:
  push:
    tags: ['v*']
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      # CUDA plus PyTorch is large. The default runner otherwise runs out of disk.
      - name: Free disk space
        run: |
          sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc
          sudo rm -rf /usr/local/share/boost "$AGENT_TOOLSDIRECTORY"

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Tag and image name
        id: meta
        run: |
          if [ "${{ github.ref_type }}" = "tag" ]; then
            echo "tag=${{ github.ref_name }}" >> "$GITHUB_OUTPUT"
          else
            echo "tag=dev-$(date +%Y%m%d)" >> "$GITHUB_OUTPUT"
          fi
          # GHCR requires lowercase; repository names may contain capitals.
          echo "repo=$(echo '${{ github.repository }}' | tr '[:upper:]' '[:lower:]')" \
              >> "$GITHUB_OUTPUT"

      - uses: docker/build-push-action@v6
        with:
          context: ./container
          push: true
          tags: |
            ghcr.io/${{ steps.meta.outputs.repo }}/<name>:${{ steps.meta.outputs.tag }}
            ghcr.io/${{ steps.meta.outputs.repo }}/<name>:latest
```

Three details that cost time when missing: the disk cleanup, the lowercase
conversion, and `packages: write`.

### 3. Build

```bash
git tag v1
git push origin v1
```

Or from the Actions tab, the "Run workflow" button, which needs no tag. Useful
when you just want to check that the build goes through. Ten to twenty minutes
is normal for a CUDA image.

### 4. Make the package reachable

Packages on GHCR are private by default, even when the repository is public.
Pulling from the cluster gives 401.

Either: the Packages tab on your profile, select the package, Package settings,
Change visibility.

Or: create a personal access token with `read:packages` and log in on the
cluster:

```bash
apptainer remote login --username <username> docker://ghcr.io
```

It asks for the token as the password.

### 5. Pull it

```bash
mkdir -p $HOME/images
apptainer pull $HOME/images/<name>.sif \
    docker://ghcr.io/<owner>/<repo>/<name>:v1
```

Apptainer fetches the Docker layers and converts them into a single `.sif`
file.

Move the cache out of your home directory first if the quota is tight:

```bash
export APPTAINER_CACHEDIR=$HOME/.apptainer/cache
export APPTAINER_TMPDIR=$HOME/.apptainer/tmp
mkdir -p $APPTAINER_CACHEDIR $APPTAINER_TMPDIR
```

## Using it

```bash
apptainer exec --nv image.sif <command>
```

| Flag or verb | What it does |
| --- | --- |
| `exec` | runs a command you supply |
| `run` | runs whatever `%runscript` defines |
| `shell` | an interactive shell inside the container, useful for debugging |
| `--nv` | exposes the host NVIDIA drivers. Without it the program sees no GPU |
| `--bind A:B` | makes host directory `A` available as `B` inside the container |

Your home directory and the working directory are bound automatically.
Everything else must be bound explicitly.

In a Slurm script:

```bash
#SBATCH --account=<project>
#SBATCH --partition=accel
#SBATCH --gpus=1
#SBATCH --mem=64G
#SBATCH --time=04:00:00

apptainer exec --nv \
    --bind $HOME/data:/data \
    $HOME/images/<name>.sif \
    <command> /data/input
```

No `module load`, no `source activate`. Everything is in the image.

Check what is inside:

```bash
apptainer exec image.sif cat /opt/versions.txt
```

Those are the lines that belong in a methods section.

## What should not go in the image

**Large data files and model weights.** They make the image unwieldy and often
need updating independently of the software. Bind them in instead, and record
their checksums separately.

**Your own analysis scripts.** They change faster than the environment. Keep
them in the repository and bind the working directory in.

**Anything secret.** An image can be copied and uploaded. Tokens and keys do
not belong there.

## Common failures

| Symptom | Cause |
| --- | --- |
| `repository name must be lowercase` | GHCR requires lowercase. Convert the repository name in the workflow |
| 401 on `apptainer pull` | The package is private. Make it public, or log in with a token |
| The program sees no GPU | `--nv` is missing |
| `no space left on device` during the build | Add the disk cleanup step to the workflow |
| My files do not exist inside the container | They sit outside your home directory. Use `--bind` |
| `apptainer: command not found` | The cluster may have it as `singularity`, or behind a module |
| Builds fine but fails on CUDA at run time | The host driver is older than the CUDA version in the image. Pick a lower base |

## When it is worth the trouble

Not when you are simply getting started. A pip environment is faster to set up
and easier to change while you are experimenting.

Do it before the runs that will be reported, and tag a version. Then the
methods section can point at an image that actually exists.

The one genuinely problematic moment is building the image *after* the results
are produced. Then it documents something other than what happened.
