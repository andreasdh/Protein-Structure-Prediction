#!/usr/bin/env python3
"""Make perturbed copies of a structure, for testing the analysis chain.

    python3 make_testcase.py ref.cif --out results/

Writes three copies whose correct scores are known in advance:

    identity  a byte-for-byte copy. Every metric must come out perfect.
    rigid     rotated and translated. Superposition must undo this, so RMSD
              must still be 0 and TM-score 1. lDDT is superposition-free, so
              it cannot be affected at all.
    rough     every atom displaced by Gaussian noise of a known width, so
              the expected RMSD is sigma*sqrt(3). The B-factor column is
              filled with a pLDDT-like value that tracks each residue's real
              error, so the calibration code must recover a strong positive
              correlation.

The copies keep the input's format and ALL of its atoms. That matters:
OpenStructure scores nothing on a CA-only file, because its residues are
missing the atoms it expects, so a CA-only test would report lDDT 0.0 and
look like a broken installation.

Only the coordinate and B-factor fields are rewritten. Everything else in the
file is passed through untouched.
"""

import argparse
import os
import shutil
import sys

import numpy as np


# --------------------------------------------------------------------- PDB

def _pdb_atoms(lines):
    """Indices of coordinate-bearing lines in a PDB file."""
    return [i for i, ln in enumerate(lines)
            if ln.startswith(("ATOM", "HETATM"))]


def read_pdb(path):
    lines = open(path).read().splitlines(keepends=True)
    idx = _pdb_atoms(lines)
    xyz = np.array([[float(lines[i][30:38]), float(lines[i][38:46]),
                     float(lines[i][46:54])] for i in idx], float)
    resnum = np.array([int(lines[i][22:26]) for i in idx])
    name = [lines[i][12:16].strip() for i in idx]
    return {"kind": "pdb", "lines": lines, "idx": idx,
            "xyz": xyz, "resnum": resnum, "name": name}


def write_pdb(st, path, xyz, bfac=None):
    lines = list(st["lines"])
    for k, i in enumerate(st["idx"]):
        ln = lines[i].rstrip("\n")
        ln = ln.ljust(80)
        x, y, z = xyz[k]
        ln = ln[:30] + f"{x:8.3f}{y:8.3f}{z:8.3f}" + ln[54:]
        if bfac is not None:
            ln = ln[:60] + f"{bfac[k]:6.2f}" + ln[66:]
        lines[i] = ln.rstrip() + "\n"
    open(path, "w").writelines(lines)


# -------------------------------------------------------------------- mmCIF

def read_cif(path):
    """Locate the atom_site loop and the columns we need to rewrite.

    mmCIF values are whitespace separated, so a row can be split and rejoined.
    Multi-line text blocks do not occur inside an atom_site loop in practice;
    if one did, this would mangle it, so the row count is checked against the
    column count below and the file is rejected rather than silently broken.
    """
    lines = open(path).read().splitlines(keepends=True)
    cols, idx, in_loop, past_header = [], [], False, False
    for i, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith("_atom_site."):
            in_loop, past_header = True, False
            cols.append(s.split(".", 1)[1])
            continue
        if not in_loop:
            continue
        if not past_header:
            if s.startswith("_") or s.startswith("loop_") or not s:
                continue
            past_header = True
        if not s or s.startswith(("#", "loop_", "_", "data_")):
            if idx:
                break
            continue
        idx.append(i)

    need = ("Cartn_x", "Cartn_y", "Cartn_z")
    if not cols or not idx or any(c not in cols for c in need):
        raise ValueError(f"{path}: could not find an atom_site loop with "
                         f"coordinates")

    ix, iy, iz = (cols.index(c) for c in need)
    iseq = next((cols.index(c) for c in ("auth_seq_id", "label_seq_id")
                 if c in cols), None)
    ib = cols.index("B_iso_or_equiv") if "B_iso_or_equiv" in cols else None
    iname = next((cols.index(c) for c in ("label_atom_id", "auth_atom_id")
                  if c in cols), None)

    rows, xyz, resnum, name = [], [], [], []
    for i in idx:
        f = lines[i].split()
        if len(f) != len(cols):
            raise ValueError(f"{path}: atom_site row {i+1} has {len(f)} "
                             f"fields but the loop declares {len(cols)}. "
                             f"This parser cannot rewrite that file.")
        rows.append(f)
        xyz.append([float(f[ix]), float(f[iy]), float(f[iz])])
        try:
            resnum.append(int(f[iseq]) if iseq is not None else 0)
        except ValueError:
            resnum.append(0)
        name.append(f[iname].strip('"') if iname is not None else "")

    return {"kind": "cif", "lines": lines, "idx": idx, "rows": rows,
            "ix": ix, "iy": iy, "iz": iz, "ib": ib,
            "xyz": np.array(xyz, float), "resnum": np.array(resnum),
            "name": name}


def write_cif(st, path, xyz, bfac=None):
    lines = list(st["lines"])
    for k, i in enumerate(st["idx"]):
        f = list(st["rows"][k])
        f[st["ix"]] = f"{xyz[k][0]:.3f}"
        f[st["iy"]] = f"{xyz[k][1]:.3f}"
        f[st["iz"]] = f"{xyz[k][2]:.3f}"
        if bfac is not None and st["ib"] is not None:
            f[st["ib"]] = f"{bfac[k]:.2f}"
        lines[i] = " ".join(f) + "\n"
    open(path, "w").writelines(lines)


def read_structure(path):
    if path.endswith((".pdb", ".ent")):
        return read_pdb(path)
    return read_cif(path)


def write_structure(st, path, xyz, bfac=None):
    if st["kind"] == "pdb":
        write_pdb(st, path, xyz, bfac)
    else:
        write_cif(st, path, xyz, bfac)


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("--out", default="results")
    ap.add_argument("--noise", type=float, default=1.0,
                    help="Gaussian sigma per coordinate, in angstrom, for the "
                         "roughened copy (default 1.0, giving RMSD ~1.73)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    st = read_structure(args.reference)
    xyz, resnum, name = st["xyz"], st["resnum"], st["name"]
    n_ca = sum(1 for x in name if x == "CA")
    print(f"   ok    read {len(xyz)} atoms ({n_ca} CA), residues "
          f"{resnum.min()}-{resnum.max()}")
    if n_ca < 20:
        print(f"   FAIL  only {n_ca} CA atoms; too small to test with")
        return 1

    os.makedirs(args.out, exist_ok=True)
    ext = os.path.splitext(args.reference)[1] or ".cif"
    rng = np.random.default_rng(args.seed)

    # identity: copy the bytes, so there is no chance of the writer itself
    # introducing a difference that the test then attributes to a tool.
    shutil.copyfile(args.reference, os.path.join(args.out,
                                                 f"identity_model_0{ext}"))

    a = np.deg2rad(37.0)
    R = np.array([[np.cos(a), -np.sin(a), 0.0],
                  [np.sin(a), np.cos(a), 0.0],
                  [0.0, 0.0, 1.0]])
    write_structure(st, os.path.join(args.out, f"rigid_model_0{ext}"),
                    xyz @ R.T + np.array([25.0, -10.0, 5.0]))

    noisy = xyz + rng.normal(0, args.noise, xyz.shape)
    # pLDDT per residue, from that residue's CA displacement, so the
    # calibration code has a correlation that is really there.
    err = np.linalg.norm(noisy - xyz, axis=1)
    ca_err = {}
    for k, nm in enumerate(name):
        if nm == "CA":
            ca_err[int(resnum[k])] = err[k]
    if ca_err:
        lo = min(ca_err.values())
        spread = max(max(ca_err.values()) - lo, 1e-9)
        plddt_by_res = {r: float(np.clip(95 - 60 * (e - lo) / spread, 30, 98))
                        for r, e in ca_err.items()}
        bfac = np.array([plddt_by_res.get(int(r), 50.0) for r in resnum])
    else:
        bfac = None
    write_structure(st, os.path.join(args.out, f"rough_model_0{ext}"),
                    noisy, bfac)

    print(f"   ok    wrote identity, rigid and rough copies to {args.out}/ "
          f"keeping all {len(xyz)} atoms")
    print(f"         expected RMSD for the roughened copy: "
          f"{args.noise * np.sqrt(3):.2f} A")
    return 0


if __name__ == "__main__":
    sys.exit(main())
