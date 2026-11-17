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

lDDT is additionally computed with OpenStructure, the reference
implementation, whenever that is available: `ost` on PATH, or an Apptainer
image named by $OST_IMAGE. Both are reported, and the lddt_source column
records which implementations ran.

OpenStructure's own --bb-lddt uses CA atoms only for peptides, so it computes
the same quantity as ca_lddt() below. The two agreeing is a check on this
implementation; the gap between backbone and all-atom lDDT shows how much of
the disagreement sits in side chains, which against a 3.16 A reference is
partly our own uncertainty rather than the model's.
"""

import argparse
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

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


# ------------------------------------------------------------- OpenStructure

def ost_command():
    """How to invoke OpenStructure here, or None if it is not available.

      1. `ost` on PATH, from a native installation
      2. $OST_IMAGE pointing at an Apptainer or Singularity image

    OpenStructure is distributed mainly as a container, so (2) is the normal
    case on a cluster:

        apptainer pull ost.sif \\
            docker://registry.scicore.unibas.ch/schwede/openstructure:latest
        export OST_IMAGE=$PWD/ost.sif

    Apptainer binds $HOME and the current directory by default, so paths
    under either are visible inside the image. Anything elsewhere needs
    --bind, so keep models, reference and output under the working directory.
    """
    if shutil.which("ost"):
        return ["ost"]
    img = os.environ.get("OST_IMAGE")
    if img and os.path.exists(img):
        for runner in ("apptainer", "singularity"):
            if shutil.which(runner):
                return [runner, "exec", img, "ost"]
    return None


def _parse_ost_local(block):
    """OST per-residue scores, keyed "<chain>.<resnum>.<inscode>".

    Returns ({resnum: value} for the chain with the most residues, number of
    chains seen), so a multi-chain model cannot silently collapse into one
    series. Residues OST could not score come back as null and are dropped.
    """
    by_chain = {}
    for k, v in (block or {}).items():
        if v is None:
            continue
        parts = k.split(".")
        if len(parts) < 2:
            continue
        if len(parts) > 2 and parts[2].strip():
            continue          # insertion code, so no plain integer numbering
        try:
            by_chain.setdefault(parts[0], {})[int(parts[1])] = float(v)
        except (TypeError, ValueError):
            continue
    if not by_chain:
        return {}, 0
    best = max(by_chain, key=lambda c: len(by_chain[c]))
    return by_chain[best], len(by_chain)


_OST_CACHE = {}


def lddt_ost(model, ref, cmd, stereochecks=True, timeout=1800):
    """lDDT from OpenStructure's compare-structures. None if it failed.

    Returns:
        aa_global : all-atom global lDDT                     (OST key lddt)
        bb_global : backbone global lDDT, CA only for peptides  (bb_lddt)
        bb_local  : {model residue number: per-residue backbone lDDT}
        clashes, bad_bonds, bad_angles : stereochemistry counts in the model

    With stereochecks on, which is the default and what CASP does, lDDT drops
    residues that fail them. A large count therefore means the score covers
    fewer residues than the sequence length suggests, so the counts are
    reported rather than hidden.

    Residue correspondence is left to OpenStructure, which aligns the
    sequences by default rather than trusting residue numbers. That handles
    the offset between a construct numbered from 1 and the reference without
    being told about it. The per-residue keys are in the MODEL's numbering.
    """
    key = (os.path.abspath(model), os.path.abspath(ref), stereochecks)
    if key in _OST_CACHE:
        return _OST_CACHE[key]

    out_json, data = None, None
    try:
        # Written into the working directory, not /tmp, because Apptainer
        # binds the working directory into the image and may not bind /tmp.
        fd, out_json = tempfile.mkstemp(suffix=".json", prefix=".ost_",
                                        dir=os.getcwd())
        os.close(fd)
        argv = cmd + ["compare-structures",
                      "-m", model, "-r", ref, "-o", out_json,
                      "--lddt", "--local-lddt",
                      "--bb-lddt", "--bb-local-lddt"]
        if not stereochecks:
            argv.append("--lddt-no-stereochecks")
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
        with open(out_json) as fh:
            data = json.load(fh)
    except Exception as e:
        tail = ""
        try:
            tail = (proc.stderr or "").strip().splitlines()[-1]
        except Exception:
            pass
        print(f"   OpenStructure failed on {os.path.basename(model)}: {e}"
              + (f" / {tail}" if tail else ""), file=sys.stderr)
        _OST_CACHE[key] = None
        return None
    finally:
        if out_json and os.path.exists(out_json):
            os.remove(out_json)

    if data.get("status") != "SUCCESS":
        tb = (data.get("traceback") or "").strip().splitlines()
        print(f"   OpenStructure returned {data.get('status')} on "
              f"{os.path.basename(model)}"
              + (f": {tb[-1]}" if tb else ""), file=sys.stderr)
        _OST_CACHE[key] = None
        return None

    local, n_chains = _parse_ost_local(data.get("bb_local_lddt"))
    if n_chains > 1:
        print(f"   note: OpenStructure scored {n_chains} chains in "
              f"{os.path.basename(model)}; using the largest.",
              file=sys.stderr)

    res = {
        "aa_global": data.get("lddt"),
        "bb_global": data.get("bb_lddt"),
        "bb_local": local,
        "clashes": data.get("model_clashes"),
        "bad_bonds": data.get("model_bad_bonds"),
        "bad_angles": data.get("model_bad_angles"),
        "ost_version": data.get("ost_version"),
    }
    _OST_CACHE[key] = res
    return res


def _n(x, nd=4):
    """Round, tolerating None."""
    return None if x is None else round(float(x), nd)


def _count(x):
    """OST reports stereochemistry problems as lists; we want how many."""
    if x is None:
        return None
    return len(x) if isinstance(x, (list, tuple)) else x


# ---------------------------------------------------------------- correspond

def align_by_resnum(nums_a, xyz_a, nums_b, xyz_b, offset=0, exclude=None):
    """Match residues by number, applying offset to the first set.

    exclude is a set of residue numbers IN THE REFERENCE NUMBERING to leave
    out of the comparison. Use it for regions where the reference itself is
    unreliable: predict the whole protein, but do not score it against
    coordinates that are poorly determined.

    Returns (xyz_a_sel, xyz_b_sel, keys) where keys are the matched residue
    numbers in the reference numbering, so callers can map results back.
    """
    exclude = exclude or set()
    idx_b = {int(n): i for i, n in enumerate(nums_b)}
    ia, ib, keys = [], [], []
    for i, n in enumerate(nums_a):
        key = int(n) + offset
        if key in idx_b and key not in exclude:
            ia.append(i)
            ib.append(idx_b[key])
            keys.append(key)
    if not ia:
        return None, None, None
    return xyz_a[ia], xyz_b[ib], keys


def excluded_residues(cfg):
    """Residue numbers to leave out of the comparison, from config.yaml.

    Expects, in reference (PDB) numbering:

        exclude_from_comparison:
          - [31, 35]
          - [40, 45]

    Returns an empty set when the key is absent, so the default is to
    compare everything.
    """
    if not cfg:
        return set()
    out = set()
    for entry in cfg.get("exclude_from_comparison", []) or []:
        start, stop = int(entry[0]), int(entry[1])
        out.update(range(start, stop + 1))
    return out


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

def mode_reference(df, ref_path, ref_chain, cfg, calib_path,
                   ost_cmd=None, ost_stereochecks=True, ost_off=False):
    rn, rx, _ = read_ca(ref_path, ref_chain)
    if rx is None:
        sys.exit(f"Could not read CA atoms from {ref_path}")
    print(f"reference: {len(rx)} CA atoms, residues {rn.min()}-{rn.max()}")

    if ost_cmd:
        print(f"lDDT: CA implementation here, plus OpenStructure via "
              f"{' '.join(ost_cmd)}"
              + ("" if ost_stereochecks else " (stereochecks off)"))
    elif ost_off:
        print("lDDT: CA implementation here only, because "
              "--no-openstructure was given.")
    else:
        print("lDDT: CA implementation here only. OpenStructure not found; "
              "install it or set $OST_IMAGE to also get the reference "
              "implementation.")

    doms = (cfg or {}).get("domains", {})
    skip = excluded_residues(cfg)
    if skip:
        print(f"excluding {len(skip)} residues from the comparison "
              f"(exclude_from_comparison in the config)")
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
        A, B, matched_global = align_by_resnum(mn, mx, rn, rx, offset=off,
                                               exclude=skip)
        if A is None or len(A) < 10:
            continue

        rmsd, _, _ = kabsch_rmsd(A, B)
        per_res = ca_lddt(A, B)
        row = {
            "predictor": r.get("predictor", "unknown"),
            "construct": r["construct"], "ligand": r.get("ligand", "none"),
            "seed": r.get("seed"), "model": r.get("model"),
            "n_matched": len(A),
            "n_excluded": len(skip),
            "rmsd_global": round(rmsd, 2),
            "lddt_global": round(float(np.nanmean(per_res)), 4),
        }
        tm = us_align(cif, ref_path, chain_r=ref_chain)
        if tm is not None:
            row["tmscore"] = round(tm, 4)

        # OpenStructure, when available. Its numbers sit alongside the CA
        # ones rather than replacing them, so earlier results stay comparable
        # and the two implementations can be checked against each other.
        ost = lddt_ost(cif, ref_path, ost_cmd,
                       stereochecks=ost_stereochecks) if ost_cmd else None
        ost_local = {}
        row["lddt_source"] = "ca+openstructure" if ost else "ca"
        if ost:
            ost_local = ost["bb_local"]
            row["lddt_ost_aa"] = _n(ost["aa_global"])
            row["lddt_ost_bb"] = _n(ost["bb_global"])
            row["n_ost_scored"] = len(ost_local)
            row["ost_clashes"] = _count(ost["clashes"])
            row["ost_bad_bonds"] = _count(ost["bad_bonds"])
            row["ost_bad_angles"] = _count(ost["bad_angles"])
            # OpenStructure has no residue-exclusion flag, so the excluded
            # regions are removed afterwards by averaging its per-residue
            # values over the residues we keep. That is not identical to a
            # global lDDT recomputed with those residues deleted, because
            # global lDDT pools all distance pairs rather than averaging
            # per-residue scores. Reported under its own name for that
            # reason. For exact comparability, trim the reference and the
            # model first and rerun.
            kept = [v for n, v in ost_local.items() if n + off not in skip]
            if kept:
                row["lddt_ost_bb_kept"] = round(float(np.mean(kept)), 4)

        # Per-domain: lDDT on the same correspondence, plus a separate
        # superposition of that domain alone. A big gap between a domain's
        # own RMSD and the global RMSD means the fold is right but the
        # placement is wrong.
        for name, (ds, de) in doms.items():
            sel = [i for i, g in enumerate(matched_global) if ds <= g <= de]
            if len(sel) < 10:
                continue
            sel = np.array(sel)
            row[f"lddt_{name}"] = round(float(np.nanmean(per_res[sel])), 4)
            d_rmsd, _, _ = kabsch_rmsd(A[sel], B[sel])
            row[f"rmsd_{name}"] = round(d_rmsd, 2)
            dv = [v for n, v in ost_local.items()
                  if ds <= n + off <= de and n + off not in skip]
            if len(dv) >= 10:
                row[f"lddt_ost_{name}"] = round(float(np.mean(dv)), 4)

        rows.append(row)

        if calib_path and len(mb) == len(mn):
            # matched_global and per_res share an index, and both already
            # have the excluded residues removed. Map each back to the
            # model's own B-factor column, which carries pLDDT.
            plddt_by_global = {int(n) + off: float(mb[i])
                               for i, n in enumerate(mn)}
            for k, g in enumerate(matched_global):
                if g not in plddt_by_global:
                    continue
                dom = next((nm for nm, (a, b) in doms.items()
                            if a <= g <= b), "")
                calib_rows.append({
                    "predictor": r.get("predictor", "unknown"),
                    "construct": r["construct"], "seed": r.get("seed"),
                    "model": r.get("model"), "resnum_global": g,
                    "domain": dom, "plddt": plddt_by_global[g],
                    "lddt": float(per_res[k]),
                    # Same residue, OpenStructure's backbone lDDT. Its keys
                    # are in the model's numbering, hence g - off.
                    "lddt_ost": ost_local.get(g - off),
                })

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

        # The same calibration against OpenStructure's lDDT. If the two
        # implementations disagree about which domain is well calibrated,
        # that is worth knowing before any of it goes in the report.
        sub_o = cal.dropna(subset=["plddt", "lddt_ost"]) \
            if "lddt_ost" in cal.columns else cal.iloc[0:0]
        if len(sub_o) > 2:
            r_o = np.corrcoef(sub_o["plddt"], sub_o["lddt_ost"])[0, 1]
            print(f"\nsame calibration against OpenStructure lDDT: "
                  f"r = {r_o:.3f}")
            if "domain" in sub_o and sub_o["domain"].any():
                for dom, g in sub_o.groupby("domain"):
                    if dom and len(g) > 2:
                        rr = np.corrcoef(g["plddt"], g["lddt_ost"])[0, 1]
                        print(f"   {dom:8s} r = {rr:6.3f}   "
                              f"mean lDDT {g['lddt_ost'].mean():.3f}")

    # Implementation check: OST's --bb-lddt is CA only for peptides, so it
    # should land close to the CA version computed here. A systematic gap
    # means one of the two is parameterised differently, not that the model
    # is better or worse than it looked.
    if not out.empty and {"lddt_global", "lddt_ost_bb"} <= set(out.columns):
        pair = out[["lddt_global", "lddt_ost_bb"]].dropna()
        if len(pair):
            d = (pair["lddt_global"] - pair["lddt_ost_bb"])
            print(f"\nCA-lDDT vs OpenStructure backbone lDDT over "
                  f"{len(pair)} models: mean difference {d.mean():+.4f}, "
                  f"largest {d.abs().max():.4f}")
            print("Close agreement means the CA implementation here is "
                  "sound. The remaining gap to lddt_ost_aa is side chains, "
                  "which the 3.16 A reference does not resolve reliably.")
    return out


def mode_consistency(df):
    rows = []
    key = ["predictor", "construct", "ligand"] if "predictor" in df.columns \
          else ["construct", "ligand"]
    for gk, g in df.groupby(key):
        c, l = (gk[-2], gk[-1]) if len(gk) == 3 else gk
        pred = gk[0] if len(gk) == 3 else "unknown"
        paths = [p for p in g["cif"] if isinstance(p, str) and os.path.exists(p)]
        if len(paths) < 2:
            continue
        vals = []
        for a, b in itertools.combinations(paths, 2):
            na, xa, _ = read_ca(a)
            nb, xb, _ = read_ca(b)
            if xa is None or xb is None:
                continue
            A, B, _ = align_by_resnum(na, xa, nb, xb)
            if A is None or len(A) < 10:
                continue
            rmsd, _, _ = kabsch_rmsd(A, B)
            tm = us_align(a, b)
            vals.append((rmsd, tm))
        if not vals:
            continue
        rms = [v[0] for v in vals]
        tms = [v[1] for v in vals if v[1] is not None]
        row = {"predictor": pred,
               "construct": c, "ligand": l, "n_models": len(paths),
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
    ap.add_argument("--no-openstructure", action="store_true",
                    help="skip OpenStructure even if it is available, and "
                         "report only the CA implementation")
    ap.add_argument("--ost-image", default=None,
                    help="Apptainer image holding OpenStructure. Same effect "
                         "as setting $OST_IMAGE")
    ap.add_argument("--ost-no-stereochecks", action="store_true",
                    help="pass --lddt-no-stereochecks to OpenStructure. Use "
                         "this when comparing the two implementations, since "
                         "the CA version here filters nothing; leave it off "
                         "for numbers going in the report, where the default "
                         "matches CASP")
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

    if args.ost_image:
        os.environ["OST_IMAGE"] = args.ost_image
    ost_cmd = None if args.no_openstructure else ost_command()

    if args.consistency:
        out = mode_consistency(df)
        label = "consistency across seeds and samples"
    elif args.reference:
        ref_chain = (cfg or {}).get("target", {}).get("reference_chain")
        out = mode_reference(df, args.reference, ref_chain, cfg,
                             args.calibration, ost_cmd=ost_cmd,
                             ost_stereochecks=not args.ost_no_stereochecks,
                             ost_off=args.no_openstructure)
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
