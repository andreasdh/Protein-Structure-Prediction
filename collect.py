#!/usr/bin/env python3
"""Walk a Boltz-2 results tree and build one table with a row per model.

Usage:
    python3 collect.py --results results/ --out summary.csv
    python3 collect.py --results results/ --out summary.csv --config config.yaml

With --config, per-domain mean pLDDT and inter-domain mean PAE are added for
constructs that contain whole domains.

This measures confidence and consistency, NOT accuracy. Nothing here tells you
whether a structure is right. For that you need compare.py against 9S11, and
for the cleaved-state predictions there is no ground truth at all.
"""

import argparse
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

try:
    import yaml
except ImportError:
    yaml = None


def find_models(results_dir):
    """Yield dicts describing every predicted model under results_dir."""
    pattern = os.path.join(results_dir, "**", "confidence_*.json")
    for conf_path in sorted(glob.glob(pattern, recursive=True)):
        d = os.path.dirname(conf_path)
        base = os.path.basename(conf_path)
        m = re.match(r"confidence_(.+)_model_(\d+)\.json$", base)
        if not m:
            continue
        stem, model_idx = m.group(1), int(m.group(2))

        cif = os.path.join(d, f"{stem}_model_{model_idx}.cif")
        if not os.path.exists(cif):
            alt = os.path.join(d, f"{stem}_model_{model_idx}.pdb")
            cif = alt if os.path.exists(alt) else ""

        pae = ""
        for cand in (f"pae_{stem}_model_{model_idx}.npz",
                     f"pae_{stem}_model_{model_idx}.npy"):
            p = os.path.join(d, cand)
            if os.path.exists(p):
                pae = p
                break

        aff = ""
        for cand in (f"affinity_{stem}.json",
                     f"affinity_{stem}_model_{model_idx}.json"):
            p = os.path.join(d, cand)
            if os.path.exists(p):
                aff = p
                break

        yield {"confidence": conf_path, "cif": cif, "pae": pae,
               "affinity": aff, "stem": stem, "model": model_idx}


def parse_seed(path):
    m = re.search(r"seed(\d+)", path)
    return int(m.group(1)) if m else -1


def parse_construct_ligand(stem):
    if "__" in stem:
        c, l = stem.split("__", 1)
        return c, l
    return stem, "none"


def load_pae(path):
    if not path:
        return None
    try:
        if path.endswith(".npz"):
            z = np.load(path)
            for k in ("pae", "predicted_aligned_error"):
                if k in z:
                    return np.asarray(z[k], float)
            return np.asarray(z[z.files[0]], float)
        return np.asarray(np.load(path), float)
    except Exception as e:
        print(f"   could not read PAE {path}: {e}", file=sys.stderr)
        return None


def plddt_from_cif(path):
    """Per-residue pLDDT from the B-factor column of a predicted model.

    Boltz writes mmCIF; we read the atom_site loop generically rather than
    depending on a parser being installed.
    """
    if not path or not os.path.exists(path):
        return None
    if path.endswith(".pdb"):
        vals = []
        for line in open(path):
            if line.startswith("ENDMDL"):
                break
            if line.startswith("ATOM") and line[12:16].strip() == "CA":
                try:
                    vals.append(float(line[60:66]))
                except ValueError:
                    pass
        return np.array(vals) if vals else None

    # mmCIF: locate the atom_site loop and its column order.
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
    if not rows or not cols:
        return None
    try:
        i_atom = cols.index("label_atom_id")
        i_b = cols.index("B_iso_or_equiv")
    except ValueError:
        return None
    vals = []
    for r in rows:
        if len(r) <= max(i_atom, i_b):
            continue
        if r[i_atom].strip('"') == "CA":
            try:
                vals.append(float(r[i_b]))
            except ValueError:
                pass
    return np.array(vals) if vals else None


def domain_slices(cfg, construct):
    """Map domain name -> (start, stop) indices WITHIN the construct, 0-based.

    Returns {} when the construct does not consist of whole domains.
    """
    if not cfg:
        return {}
    regions = cfg["constructs"].get(construct, {}).get("regions")
    doms = cfg.get("domains", {})
    if not regions or not doms:
        return {}

    # Build the global->local residue mapping for this construct.
    mapping, local = {}, 0
    for start, stop in regions:
        for g in range(start, stop + 1):
            mapping[g] = local
            local += 1

    out = {}
    for name, (ds, de) in doms.items():
        if ds in mapping and de in mapping:
            out[name] = (mapping[ds], mapping[de])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default="summary.csv")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = None
    if args.config:
        if yaml is None:
            sys.exit("pyyaml not installed; drop --config or pip install pyyaml")
        with open(args.config) as fh:
            cfg = yaml.safe_load(fh)

    records = []
    for m in find_models(args.results):
        with open(m["confidence"]) as fh:
            conf = json.load(fh)

        construct, ligand = parse_construct_ligand(m["stem"])
        rec = {
            "construct": construct,
            "ligand": ligand,
            "seed": parse_seed(m["confidence"]),
            "model": m["model"],
            "confidence_score": conf.get("confidence_score"),
            "ptm": conf.get("ptm"),
            "iptm": conf.get("iptm"),
            "complex_plddt": conf.get("complex_plddt"),
            "ligand_iptm": conf.get("ligand_iptm"),
            "protein_iptm": conf.get("protein_iptm"),
            "cif": m["cif"],
            "pae_file": m["pae"],
        }

        plddt = plddt_from_cif(m["cif"])
        if plddt is not None:
            rec["n_res"] = len(plddt)
            rec["plddt_mean"] = float(plddt.mean())
            rec["plddt_frac_below_70"] = float((plddt < 70).mean())
            for name, (a, b) in domain_slices(cfg, construct).items():
                if b < len(plddt):
                    rec[f"plddt_{name}"] = float(plddt[a:b + 1].mean())

        pae = load_pae(m["pae"])
        if pae is not None:
            rec["pae_mean"] = float(pae.mean())
            slices = domain_slices(cfg, construct)
            names = sorted(slices)
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    a1, b1 = slices[names[i]]
                    a2, b2 = slices[names[j]]
                    if b1 < pae.shape[0] and b2 < pae.shape[0]:
                        blk = pae[a1:b1 + 1, a2:b2 + 1]
                        rec[f"pae_{names[i]}_{names[j]}"] = float(blk.mean())

        if m["affinity"]:
            with open(m["affinity"]) as fh:
                aff = json.load(fh)
            y = aff.get("affinity_pred_value")
            rec["affinity_pred_value"] = y
            rec["affinity_prob_binary"] = aff.get("affinity_probability_binary")
            if y is not None:
                rec["pIC50_pred"] = 6.0 - float(y)

        records.append(rec)

    if not records:
        sys.exit(f"No Boltz output found under {args.results}/. "
                 "Expected files named confidence_*.json.")

    df = pd.DataFrame(records).sort_values(
        ["construct", "ligand", "seed", "model"]).reset_index(drop=True)
    df.to_csv(args.out, index=False)
    print(f"wrote {args.out}: {len(df)} models")

    cols = [c for c in ("plddt_mean", "confidence_score", "ptm", "iptm")
            if c in df.columns]
    if cols:
        print("\nper construct (mean across seeds and samples):")
        print(df.groupby(["construct", "ligand"])[cols].mean().round(3).to_string())

    dom_cols = sorted(c for c in df.columns if c.startswith("plddt_")
                      and c not in ("plddt_mean", "plddt_frac_below_70"))
    if dom_cols:
        print("\nper-domain mean pLDDT:")
        print(df.groupby("construct")[dom_cols].mean().round(1).to_string())

    print("\nReminder: these are confidence numbers. They do not tell you "
          "whether a structure is correct.")


if __name__ == "__main__":
    main()
