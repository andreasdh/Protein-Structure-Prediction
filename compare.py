#!/usr/bin/env python3
"""Compare predicted structures to a reference, and to each other.

Three things, which answer three different questions:

  --reference REF   accuracy      : how close is the prediction to 9S11
  --consistency     reproducibility: how much do seeds agree with each other
  --calibration     honesty       : does confidence track accuracy

Usage:
    python3 compare.py --summary summary.csv --reference reference/9s11.cif \\
        --config config.yaml --out comparison.csv --calibration calib.csv
    python3 compare.py --summary summary.csv --consistency --out consistency.csv

TM-score is computed with US-align if it is on PATH. RMSD and lDDT are
computed here, on CA atoms, so the script works with nothing installed.
CA-lDDT is not identical to the all-atom lDDT that OpenStructure reports;
use OpenStructure for anything going into a paper.
"""

import argparse
import itertools
import os
import re
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd

try:
    import yaml
except ImportError:
    yaml = None


# ---------------------------------------------------------------- structure IO

def read_ca(path, chain=None):
    """Return (resnums, coords Nx3, bfactors) for CA atoms of the first model."""
    if not os.path.exists(path):
        return None, None, None
    if path.endswith((".pdb", ".ent")):
        return _read_ca_pdb(path, chain)
    return _read_ca_cif(path, chain)


def _read_ca_pdb(path, chain):
    nums, xyz, bf = [], [], []
    for line in open(path):
        if line.startswith("ENDMDL"):
            break
        if not line.startswith("ATOM"):
            continue
        if line[16] not in (" ", "A"):
            continue
        if line[12:16].strip() != "CA":
            continue
        if chain and line[21] != chain:
            continue
        nums.append(int(line[22:26]))
        xyz.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
        bf.append(float(line[60:66]) if len(line) >= 66 else 0.0)
    return np.array(nums), np.array(xyz, float), np.array(bf)


def _read_ca_cif(path, chain):
    cols, rows, in_loop, header = [], [], False, True
    with open(path) as fh:
        for line in fh:
            s = line.strip()
            if s.startswith("_atom_site."):
                in_loop, header = True, True
                cols.append(s.split(".", 1)[1])
                continue
            if in_loop:
                if header and (s.startswith("_") or s.startswith("loop_")):
                    continue
                if not s or s.startswith("#") or s.startswith("loop_"):
                    if rows:
                        break
                    continue
                header = False
                rows.append(s.split())
    if not rows:
        return None, None, None

    def idx(*names):
        for n in names:
            if n in cols:
                return cols.index(n)
        return None

    i_atom = idx("label_atom_id")
    i_x, i_y, i_z = idx("Cartn_x"), idx("Cartn_y"), idx("Cartn_z")
    i_seq = idx("label_seq_id", "auth_seq_id")
    i_ch = idx("auth_asym_id", "label_asym_id")
    i_b = idx("B_iso_or_equiv")
    i_model = idx("pdbx_PDB_model_num")
    i_alt = idx("label_alt_id")
    if None in (i_atom, i_x, i_y, i_z, i_seq):
        return None, None, None

    nums, xyz, bf = [], [], []
    first_model = None
    for r in rows:
        if len(r) <= max(x for x in (i_atom, i_x, i_y, i_z, i_seq, i_b or 0,
                                     i_ch or 0, i_model or 0, i_alt or 0)):
            continue
        if i_model is not None:
            if first_model is None:
                first_model = r[i_model]
            elif r[i_model] != first_model:
                break
        if i_alt is not None and r[i_alt] not in (".", "?", "A"):
            continue
        if r[i_atom].strip('"') != "CA":
            continue
        if chain and i_ch is not None and r[i_ch] != chain:
            continue
        try:
            nums.append(int(r[i_seq]))
            xyz.append((float(r[i_x]), float(r[i_y]), float(r[i_z])))
            bf.append(float(r[i_b]) if i_b is not None else 0.0)
        except ValueError:
            continue
    return np.array(nums), np.array(xyz, float), np.array(bf)


# ------------------------------------------------------------------- geometry

def kabsch_rmsd(P, Q):
    """RMSD after optimal superposition of P onto Q."""
    Pc, Qc = P - P.mean(0), Q - Q.mean(0)
    V, _, Wt = np.linalg.svd(Pc.T @ Qc)
    d = np.sign(np.linalg.det(V @ Wt))
    D = np.diag([1.0, 1.0, d])
    R = V @ D @ Wt
    Pr = Pc @ R
    return float(np.sqrt(((Pr - Qc) ** 2).sum() / len(P))), Pr, Qc


def ca_lddt(model_xyz, ref_xyz, inclusion_radius=15.0,
            thresholds=(0.5, 1.0, 2.0, 4.0), seq_sep=1):
    """Per-residue CA-lDDT. Superposition-free by construction.

    model_xyz and ref_xyz must already be in 1:1 residue correspondence.
    """
    n = len(ref_xyz)
    dref = np.linalg.norm(ref_xyz[:, None] - ref_xyz[None, :], axis=-1)
    dmod = np.linalg.norm(model_xyz[:, None] - model_xyz[None, :], axis=-1)

    sep = np.abs(np.subtract.outer(np.arange(n), np.arange(n)))
    mask = (dref < inclusion_radius) & (sep >= seq_sep)

    diff = np.abs(dmod - dref)
    preserved = np.zeros((n, n), float)
    for t in thresholds:
        preserved += (diff < t)
    preserved /= len(thresholds)

    per_res = np.full(n, np.nan)
    counts = mask.sum(axis=1)
    nz = counts > 0
    per_res[nz] = (preserved * mask).sum(axis=1)[nz] / counts[nz]
    return per_res


def us_align(model, ref, chain_m=None, chain_r=None):
    """TM-score via US-align, normalised by the reference length. None if absent."""
    exe = None
    for cand in ("USalign", "US-align", "us-align", "TMalign"):
        if shutil.which(cand):
            exe = cand
            break
    if exe is None:
        return None
    cmd = [exe, model, ref]
    if chain_m:
        cmd += ["-chain1", chain_m]
    if chain_r:
        cmd += ["-chain2", chain_r]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=300).stdout
    except Exception:
        return None
    scores = [float(m.group(1)) for m in
              re.finditer(r"TM-score=\s*([0-9.]+)", out)]
    # Second value is normalised by the second structure, i.e. the reference.
    if len(scores) >= 2:
        return scores[1]
    return scores[0] if scores else None


# ---------------------------------------------------------------- correspond

def align_by_resnum(nums_a, xyz_a, nums_b, xyz_b, offset=0):
    """Match residues by number, applying offset to the first set."""
    idx_b = {int(n): i for i, n in enumerate(nums_b)}
    ia, ib = [], []
    for i, n in enumerate(nums_a):
        key = int(n) + offset
        if key in idx_b:
            ia.append(i)
            ib.append(idx_b[key])
    if not ia:
        return None, None
    return xyz_a[ia], xyz_b[ib]


def construct_offset(cfg, construct):
    """Residue number of the first residue of the construct, minus 1.

    Boltz numbers its output from 1, so this converts local numbering back to
    the numbering of the full-length sequence. Only valid for single-region
    constructs; returns None otherwise.
    """
    if not cfg:
        return 0
    regions = cfg["constructs"].get(construct, {}).get("regions")
    if not regions:
        return 0
    if len(regions) > 1:
        return None
    return regions[0][0] - 1


# ---------------------------------------------------------------------- modes

def mode_reference(df, ref_path, ref_chain, cfg, calib_path):
    rn, rx, _ = read_ca(ref_path, ref_chain)
    if rx is None:
        sys.exit(f"Could not read CA atoms from {ref_path}")
    print(f"reference: {len(rx)} CA atoms, residues {rn.min()}-{rn.max()}")

    doms = (cfg or {}).get("domains", {})
    rows, calib_rows = [], []

    for _, r in df.iterrows():
        cif = r.get("cif")
        if not isinstance(cif, str) or not os.path.exists(cif):
            continue
        mn, mx, mb = read_ca(cif)
        if mx is None:
            continue

        off = construct_offset(cfg, r["construct"])
        if off is None:
            continue
        A, B = align_by_resnum(mn, mx, rn, rx, offset=off)
        if A is None or len(A) < 10:
            continue

        rmsd, _, _ = kabsch_rmsd(A, B)
        per_res = ca_lddt(A, B)
        row = {
            "construct": r["construct"], "ligand": r.get("ligand", "none"),
            "seed": r.get("seed"), "model": r.get("model"),
            "n_matched": len(A),
            "rmsd_global": round(rmsd, 2),
            "lddt_global": round(float(np.nanmean(per_res)), 4),
        }
        tm = us_align(cif, ref_path, chain_r=ref_chain)
        if tm is not None:
            row["tmscore"] = round(tm, 4)

        # Per-domain: lDDT on the same correspondence, plus a separate
        # superposition of that domain alone. A big gap between a domain's
        # own RMSD and the global RMSD means the fold is right but the
        # placement is wrong.
        matched_global = [int(n) + off for n in mn
                          if int(n) + off in set(int(x) for x in rn)]
        for name, (ds, de) in doms.items():
            sel = [i for i, g in enumerate(matched_global) if ds <= g <= de]
            if len(sel) < 10:
                continue
            sel = np.array(sel)
            row[f"lddt_{name}"] = round(float(np.nanmean(per_res[sel])), 4)
            d_rmsd, _, _ = kabsch_rmsd(A[sel], B[sel])
            row[f"rmsd_{name}"] = round(d_rmsd, 2)

        rows.append(row)

        if calib_path and len(mb) == len(mn):
            keep = {int(n) + off for n in mn} & {int(x) for x in rn}
            k = 0
            for i, n in enumerate(mn):
                if int(n) + off in keep:
                    g = int(n) + off
                    dom = next((nm for nm, (a, b) in doms.items()
                                if a <= g <= b), "")
                    calib_rows.append({
                        "construct": r["construct"], "seed": r.get("seed"),
                        "model": r.get("model"), "resnum_global": g,
                        "domain": dom, "plddt": float(mb[i]),
                        "lddt": float(per_res[k]),
                    })
                    k += 1

    out = pd.DataFrame(rows)
    if calib_path and calib_rows:
        cal = pd.DataFrame(calib_rows)
        cal.to_csv(calib_path, index=False)
        print(f"wrote {calib_path}: {len(cal)} residue observations")
        sub = cal.dropna(subset=["plddt", "lddt"])
        if len(sub) > 2:
            r_all = np.corrcoef(sub["plddt"], sub["lddt"])[0, 1]
            print(f"\nconfidence calibration, all residues: r = {r_all:.3f}")
            if "domain" in sub and sub["domain"].any():
                print("by domain:")
                for dom, g in sub.groupby("domain"):
                    if dom and len(g) > 2:
                        rr = np.corrcoef(g["plddt"], g["lddt"])[0, 1]
                        hi_wrong = ((g["plddt"] > 80) & (g["lddt"] < 0.6)).mean()
                        print(f"   {dom:8s} r = {rr:6.3f}   "
                              f"mean pLDDT {g['plddt'].mean():5.1f}   "
                              f"mean lDDT {g['lddt'].mean():.3f}   "
                              f"confidently wrong: {hi_wrong:5.1%}")
            print("\n'Confidently wrong' = pLDDT above 80 with lDDT below 0.6.")
            print("A high figure for DUF is the result the project is after.")
    return out


def mode_consistency(df):
    rows = []
    for (c, l), g in df.groupby(["construct", "ligand"]):
        paths = [p for p in g["cif"] if isinstance(p, str) and os.path.exists(p)]
        if len(paths) < 2:
            continue
        vals = []
        for a, b in itertools.combinations(paths, 2):
            na, xa, _ = read_ca(a)
            nb, xb, _ = read_ca(b)
            if xa is None or xb is None:
                continue
            A, B = align_by_resnum(na, xa, nb, xb)
            if A is None or len(A) < 10:
                continue
            rmsd, _, _ = kabsch_rmsd(A, B)
            tm = us_align(a, b)
            vals.append((rmsd, tm))
        if not vals:
            continue
        rms = [v[0] for v in vals]
        tms = [v[1] for v in vals if v[1] is not None]
        row = {"construct": c, "ligand": l, "n_models": len(paths),
               "n_pairs": len(vals),
               "rmsd_pairwise_mean": round(float(np.mean(rms)), 2),
               "rmsd_pairwise_max": round(float(np.max(rms)), 2)}
        if tms:
            row["tm_pairwise_mean"] = round(float(np.mean(tms)), 4)
            row["tm_pairwise_min"] = round(float(np.min(tms)), 4)
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default="summary.csv")
    ap.add_argument("--reference", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--consistency", action="store_true")
    ap.add_argument("--calibration", default=None,
                    help="write per-residue pLDDT vs lDDT to this CSV")
    ap.add_argument("--out", default="comparison.csv")
    args = ap.parse_args()

    if not os.path.exists(args.summary):
        sys.exit(f"{args.summary} not found. Run collect.py first.")
    df = pd.read_csv(args.summary)

    cfg = None
    if args.config:
        if yaml is None:
            sys.exit("pyyaml not installed")
        with open(args.config) as fh:
            cfg = yaml.safe_load(fh)

    if shutil.which("USalign") is None and shutil.which("TMalign") is None:
        print("note: US-align not on PATH, so TM-score is skipped. "
              "RMSD and CA-lDDT still work.\n")

    if args.consistency:
        out = mode_consistency(df)
        label = "consistency across seeds and samples"
    elif args.reference:
        ref_chain = (cfg or {}).get("target", {}).get("reference_chain")
        out = mode_reference(df, args.reference, ref_chain, cfg, args.calibration)
        label = "accuracy against the reference"
    else:
        sys.exit("Give --reference REF or --consistency.")

    if out.empty:
        sys.exit("Nothing to compare. Check that the cif paths in the summary "
                 "exist and that the construct regions in the config are right.")

    out.to_csv(args.out, index=False)
    print(f"\nwrote {args.out}: {len(out)} rows ({label})")
    with pd.option_context("display.width", 200, "display.max_columns", 50):
        print(out.to_string(index=False))


if __name__ == "__main__":
    main()
