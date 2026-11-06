# Boltz-2 environment.
#
# Built as a Docker image because that is what CI runners can build, then
# pulled with Apptainer on the cluster, which converts it to a .sif:
#
#     apptainer pull boltz.sif docker://ghcr.io/<owner>/<repo>/boltz:v2
#
# BASE IMAGE:
# Ubuntu 22.04 ships python3.11 as 3.11.0~rc1, a release candidate predating
# sys.get_int_max_str_digits. Recent PyTorch calls that at import time, so a
# 22.04 base fails with:
#     AttributeError: module 'sys' has no attribute 'get_int_max_str_digits'
# Ubuntu 24.04 ships a real Python 3.12.3, the same version known to work in
# the pip environment on Fox.
#
# VIRTUAL ENVIRONMENT:
# Ubuntu 24.04 marks its system Python as externally managed (PEP 668). Pip
# refuses to touch it, and forcing it fails anyway, because Ubuntu's own pip
# was installed by dpkg and carries no RECORD file for pip to uninstall:
#     ERROR: Cannot uninstall pip 24.0, RECORD file not found.
# A venv at /opt/venv sidesteps all of it. Putting it first on PATH makes it
# the default python, so nothing downstream has to know it is there.
#
# Model weights are deliberately NOT baked in: they are several GB, and
# keeping them outside lets you record their checksums separately. Bind
# BOLTZ_CACHE in at run time.

FROM nvidia/cuda:13.3.1-cudnn-runtime-ubuntu24.04

LABEL org.opencontainers.image.description="Boltz-2 structure prediction environment"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-dev \
        build-essential \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Fail loudly here rather than several layers later if the base ever changes.
RUN python3 -c "import sys; assert sys.version_info[:2] >= (3, 12), sys.version; \
    assert hasattr(sys, 'get_int_max_str_digits'), 'release-candidate Python'; \
    print('python ok:', sys.version)"

# Everything from here uses the venv, because it is first on PATH.
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
ENV VIRTUAL_ENV=/opt/venv

RUN pip install --upgrade pip setuptools wheel

# --- pinned versions -------------------------------------------------------
# Change these deliberately, tag a new release, and note it in the logbook.
ARG BOLTZ_VERSION=2.2.1

RUN pip install \
        "boltz==${BOLTZ_VERSION}" \
        cuequivariance-torch \
        cuequivariance-ops-torch-cu12

# Analysis dependencies, so the same image runs collect.py and compare.py.
RUN pip install \
        numpy \
        pandas \
        pyyaml \
        gemmi \
        biopython

# Prove the thing actually imports, at build time rather than on the cluster.
# This is the check that would have caught the 22.04 problem in CI.
RUN boltz --help > /dev/null && echo "boltz imports cleanly"

ENV BOLTZ_CACHE=/opt/boltz_cache
RUN mkdir -p /opt/boltz_cache

# Record what landed, so `apptainer exec img.sif cat /opt/versions.txt`
# answers the question without starting Python.
RUN pip list --format=freeze > /opt/versions.txt \
    && python3 -c "import sys, torch; \
        print('python', sys.version.split()[0]); \
        print('torch', torch.__version__)" >> /opt/versions.txt \
    && echo "cuda_base nvidia/cuda:13.3.1-cudnn-runtime-ubuntu24.04" >> /opt/versions.txt

RUN wget -q https://zhanggroup.org/US-align/bin/module/USalign.cpp -O /tmp/USalign.cpp \
    && g++ -O3 -ffast-math -lm -o /usr/local/bin/USalign /tmp/USalign.cpp \
    && rm /tmp/USalign.cpp
    
WORKDIR /work
CMD ["boltz", "--help"]
