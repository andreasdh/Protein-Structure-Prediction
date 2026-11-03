# Handing a container to collaborators

Once an image exists, the people who use it should not have to think about it.
This describes how to distribute one, how to make it invisible in daily work,
and the one trap worth avoiding.

## Distributing the image

An image is a single file, so there are three routes. Pick by how much you
trust the network and how many people are involved.

**Pull from a registry.** Each person runs one command:

```bash
apptainer pull $HOME/images/<name>.sif \
    docker://ghcr.io/<owner>/<repo>/<name>:v1
```

Best when the package is public, or when everyone has a token. The version tag
in that command is the version they ran, which makes it self-documenting.

**Copy the file.** Build once, then:

```bash
scp <name>.sif user@cluster:~/images/
```

Works when the registry is unreachable or the package must stay private.
Nobody needs registry credentials. The cost is that a copied file carries no
record of where it came from, so note the tag and digest alongside it.

**Put it on shared storage.** One copy in a project directory everyone can
read:

```bash
/projects/<project>/images/<name>-v1.sif
```

Best for a group. Keep the version in the filename and never overwrite an
existing file. Someone's run that silently changes software underneath it is a
reproducibility failure that is very hard to diagnose afterwards.

## Making it invisible

Nobody should have to remember Apptainer syntax to do their work. A small
wrapper hides it:

```bash
#!/bin/bash
set -euo pipefail

IMAGE="${IMAGE:-$HOME/images/<name>.sif}"
VENV="${VENV:-$HOME/envs/<name>}"

if [ -f "$IMAGE" ]; then
    echo "[run] container: $IMAGE" >&2
    exec apptainer exec --nv "$IMAGE" <command> "$@"
else
    echo "[run] local environment: $VENV" >&2
    echo "[run] NOTE: not the frozen image." >&2
    source "$VENV/bin/activate"
    exec <command> "$@"
fi
```

Everyone then runs the same line whether or not the image is present:

```bash
./run.sh --some-argument
```

Two properties make this worth the twenty lines. The command is identical
either way, so switching is a setting rather than a different way of working.
And the script announces which path it took, so the choice ends up in the log
rather than in someone's memory.

## The trap: switching late

The tempting plan is to develop in a quick local environment and switch to the
container at the end, for the paper.

This goes wrong when the two are not the same software. The image then
documents an environment that did not produce the results, which is worse than
having no image at all, because it looks authoritative.

Three measures remove the problem.

**Freeze the local environment against the image.** One comparison:

```bash
apptainer exec image.sif cat /opt/versions.txt > image_versions.txt
pip list --format=freeze > local_versions.txt
diff <(sort image_versions.txt) <(sort local_versions.txt)
```

If the packages that matter agree, the two are the same stack and it does not
matter which ran what.

**Run one input both ways.** Same input, same seed, once in each environment,
then compare the outputs. Put the number in the logbook. It is cheap insurance
and it is something a reviewer may ask about.

Do not expect identical files. GPU computation is not bit-reproducible across
card models and driver versions, so what you are checking is that the results
agree to within the noise you would see anyway, not that the bytes match.

**Use the image for anything reported.** Exploration in a local environment is
fine. Any run that produces a number in the write-up should go through the
image, and whatever records provenance should capture which image it was.

## Timing

Build the image early, while the setup is fresh in your mind, and let people
use the wrapper from the first day. Then the container costs them nothing and
the question of switching never arises.

The only moment to avoid is building the image after the results exist.

## What collaborators need to know

In most cases, one line:

```bash
./run.sh <arguments>
```

They should learn what the container is before writing a methods section, but
that is a later conversation. It does not need to block anyone from starting.

## What to record

Whenever a run produces a number that will be reported:

| Item | Where it comes from |
| --- | --- |
| Image tag | the pull command, or the filename |
| Image digest | `sha256:...`, from the build log or `apptainer inspect` |
| Commit of the code | `git rev-parse HEAD` |
| Version list | `apptainer exec image.sif cat /opt/versions.txt` |
| Host GPU and driver | `nvidia-smi` |

That set is enough for someone else to reconstruct the run, and it is what a
methods section needs.
