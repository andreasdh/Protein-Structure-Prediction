# Boltz-2 environment for the CCTX2 project.
#
# Built as a Docker image because that is what CI runners can build, then
# pulled on Fox with Apptainer, which converts it to a .sif automatically:
#
#     apptainer pull boltz.sif docker://ghcr.io/<owner>/<repo>/boltz:v1
#
# Every version is pinned. The point of this file is that it produces the
# same environment in two years as it does today; an unpinned version would
# defeat that.
#
# Model weights are deliberately NOT baked in: they are several GB, and
# keeping them outside lets you record their checksums separately. Bind
# BOLTZ_CACHE in at run time.

FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

LABEL org.opencontainers.image.description="Boltz-2 for the CCTX2 structure prediction project"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-venv \
        python3.11-dev \
        python3-pip \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && python3 -m pip install --upgrade pip setuptools wheel

# --- pinned versions -------------------------------------------------------
# Change these deliberately, tag a new release, and note the change in the
# logbook. Do not loosen them.
ARG BOLTZ_VERSION=2.2.1

RUN python3 -m pip install \
        "boltz==${BOLTZ_VERSION}" \
        cuequivariance-torch \
        cuequivariance-ops-torch-cu12

# Analysis dependencies, so the same image runs collect.py and compare.py.
RUN python3 -m pip install \
        numpy \
        pandas \
        pyyaml \
        gemmi \
        biopython

# Default cache location. Override with --env or bind a host directory here.
ENV BOLTZ_CACHE=/opt/boltz_cache
RUN mkdir -p /opt/boltz_cache

# Record what actually landed, so `apptainer exec img.sif cat /opt/versions.txt`
# answers the question without starting Python.
RUN python3 -m pip list --format=freeze > /opt/versions.txt \
    && python3 -c "import torch; print('torch', torch.__version__)" >> /opt/versions.txt \
    && echo "cuda_base nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04" >> /opt/versions.txt

WORKDIR /work
CMD ["boltz", "--help"]
