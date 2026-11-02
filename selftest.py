#!/usr/bin/env python3
"""Build a fake Boltz-2 output tree and run the whole pipeline on it.

This verifies the scripts work before you have any real results, and it is
also the fastest way to see what the output tables look like.

    python3 selftest.py

Writes everything into _selftest/ and deletes it on a clean run.
"""
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "_selftest")

AA = "ACDEFGHIKLMNPQRSTVWY"
L = 300
DOMAINS = {"BTF1": [1, 80], "BTF2": [81, 160], "DUF": [161, 300]}


def helix(n, origin, axis, radius=2.3, rise=1.5, twist=np.deg2rad(100), phase=0.0):
    axis = np.asarray(axis, float)
    axis /= np.linalg.norm(axis)
    tmp = np.array([1.0, 0, 0]) if abs(axis[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(axis, tmp)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(axis, e1)
    t = np.arange(n)
    return (np.asarray(origin, float) + np.outer(t * rise, axis)
            + radius * (np.cos(twist * t + phase)[:, None] * e1
                        + np.sin(twist * t + phase)[:, None] * e2))


def reference_coords():
    """Three 'domains' as separated helical bundles."""
    a = helix(80, (0, 0, 0), (0, 0, 1))
    b = helix(80, (16, 0, 0), (0, 0, 1), phase=np.pi)
    c = helix(140, (8, 20, 0), (0, 0, 1))
    return np.vstack([a, b, c])


def perturb(xyz, rng, good_noise=0.4, bad_noise=3.0, shift=None):
    """BTF domains close to reference, DUF scrambled and displaced."""
    out = xyz.copy()
    out[:160] += rng.normal(0, good_noise, (160, 3))
    out[160:] += rng.normal(0, bad_noise, (len(out) - 160, 3))
    if shift is not None:
        out[160:] += shift
    return out


def write_cif(path, coords, bfac, start=1):
    lines = ["data_model", "#", "loop_",
             "_atom_site.group_PDB", "_atom_site.id", "_atom_site.type_symbol",
             "_atom_site.label_atom_id", "_atom_site.label_comp_id",
             "_atom_site.label_asym_id", "_atom_site.label_seq_id",
             "_atom_site.Cartn_x", "_atom_site.Cartn_y", "_atom_site.Cartn_z",
             "_atom_site.occupancy", "_atom_site.B_iso_or_equiv",
             "_atom_site.auth_asym_id", "_atom_site.pdbx_PDB_model_num"]
    for i, (xyz, b) in enumerate(zip(coords, bfac)):
        n = start + i
        lines.append(
            f"ATOM {i+1} C CA ALA A {n} "
            f"{xyz[0]:.3f} {xyz[1]:.3f} {xyz[2]:.3f} 1.00 {b:.2f} A 1")
    lines.append("#")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    if os.path.exists(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)
    os.chdir(WORK)
    rng = np.random.default_rng(0)

    # --- inputs the user would normally supply -----------------------------
    os.makedirs("sequences", exist_ok=True)
    os.makedirs("reference", exist_ok=True)
    seq = "".join(rng.choice(list(AA), L))
    with open("sequences/cctx2.fasta", "w") as fh:
        fh.write(">TEST\n" + "\n".join(seq[i:i+60] for i in range(0, L, 60)) + "\n")

    ref = reference_coords()
    write_cif("reference/ref.cif", ref, np.full(L, 30.0))

    cfg = {
        "target": {"name": "TEST", "fasta": "sequences/cctx2.fasta",
                   "reference_pdb": "reference/ref.cif", "reference_chain": "A"},
        "domains": DOMAINS,
        "constructs": {
            "full": {"regions": [[1, 300]]},
            "duf_alone": {"regions": [[161, 300]]},
        },
        "ligands": {},
        "run": {"seeds": 3, "diffusion_samples": 2, "recycling_steps": 6,
                "use_msa_server": False},
        "slurm": {"account": "ecTEST", "partition": "accel", "gpus": 1,
                  "cpus_per_task": 8, "mem": "64G", "time": "04:00:00",
                  "boltz_cache": "/tmp/cache"},
    }
    with open("config.yaml", "w") as fh:
        yaml.safe_dump(cfg, fh, sort_keys=False)

    # --- step 1: make_inputs ----------------------------------------------
    print("=" * 70, "\nmake_inputs.py\n", "=" * 70, sep="")
    r = subprocess.run([sys.executable, os.path.join(HERE, "make_inputs.py"),
                        "--config", "config.yaml", "--out", "inputs",
                        "--constructs", "full", "duf_alone"],
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)
    assert r.returncode == 0, r.stderr

    # --- step 2: make_slurm ------------------------------------------------
    print("=" * 70, "\nmake_slurm.py\n", "=" * 70, sep="")
    r = subprocess.run([sys.executable, os.path.join(HERE, "make_slurm.py"),
                        "--config", "config.yaml", "--inputs", "inputs"],
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)
    assert r.returncode == 0, r.stderr

    # --- fake Boltz output -------------------------------------------------
    # 'full': BTF good, DUF bad but CONFIDENT -> the result we are looking for
    # 'duf_alone': no reference exists; just confidence and consistency
    for stem, nres, start in (("full", 300, 1), ("duf_alone", 140, 1)):
        for seed in (1, 2, 3):
            d = os.path.join("results", stem, f"seed{seed}",
                             f"boltz_results_{stem}", "predictions", stem)
            os.makedirs(d, exist_ok=True)
            for mi in (0, 1):
                if stem == "full":
                    shift = rng.normal(0, 6, 3)
                    coords = perturb(ref, rng, shift=shift)
                    plddt = np.concatenate([
                        rng.normal(93, 2, 160),      # BTF: confident and right
                        rng.normal(88, 3, 140)])     # DUF: confident and wrong
                else:
                    coords = ref[160:] + rng.normal(0, 2.5, (140, 3))
                    plddt = rng.normal(74, 6, 140)
                plddt = np.clip(plddt, 0, 100)
                write_cif(os.path.join(d, f"{stem}_model_{mi}.cif"),
                          coords, plddt, start=start)

                n = nres
                pae = np.full((n, n), 20.0)
                if stem == "full":
                    for a, b in ((0, 80), (80, 160), (160, 300)):
                        pae[a:b, a:b] = 3.0 + rng.normal(0, .3, (b - a, b - a))
                    pae[:160, :160] = np.clip(pae[:160, :160], 0.2, None)
                    pae[0:80, 80:160] = pae[80:160, 0:80] = 6.0
                else:
                    pae[:] = 5.0 + rng.normal(0, .5, (n, n))
                np.fill_diagonal(pae, 0.2)
                np.savez(os.path.join(d, f"pae_{stem}_model_{mi}.npz"), pae=pae)

                json.dump({"confidence_score": 0.82, "ptm": 0.79,
                           "complex_plddt": float(plddt.mean() / 100)},
                          open(os.path.join(d,
                               f"confidence_{stem}_model_{mi}.json"), "w"))

    # --- step 3: collect ---------------------------------------------------
    print("=" * 70, "\ncollect.py\n", "=" * 70, sep="")
    r = subprocess.run([sys.executable, os.path.join(HERE, "collect.py"),
                        "--results", "results", "--out", "summary.csv",
                        "--config", "config.yaml"],
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)
    assert r.returncode == 0, r.stderr

    # --- step 4: compare against reference ---------------------------------
    print("=" * 70, "\ncompare.py --reference\n", "=" * 70, sep="")
    r = subprocess.run([sys.executable, os.path.join(HERE, "compare.py"),
                        "--summary", "summary.csv",
                        "--reference", "reference/ref.cif",
                        "--config", "config.yaml",
                        "--calibration", "calib.csv",
                        "--out", "comparison.csv"],
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)
    assert r.returncode == 0, r.stderr

    # --- step 5: consistency ----------------------------------------------
    print("=" * 70, "\ncompare.py --consistency\n", "=" * 70, sep="")
    r = subprocess.run([sys.executable, os.path.join(HERE, "compare.py"),
                        "--summary", "summary.csv", "--consistency",
                        "--out", "consistency.csv"],
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)
    assert r.returncode == 0, r.stderr

    print("\nALL STEPS PASSED")


if __name__ == "__main__":
    main()
