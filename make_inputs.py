#!/usr/bin/env python3
"""Generate Boltz-2 input YAML files for the CCTX2 construct/ligand matrix.

Usage:
    python3 make_inputs.py --config config.yaml --out inputs/
    python3 make_inputs.py --config config.yaml --out inputs/ \
        --constructs full duf_alone --ligands none lacdinac

One YAML per (construct, ligand) pair. Seeds are handled by Boltz at run time
via --seed, not by duplicating inputs, so the job array indexes over YAML files
and seeds together (see submit.sh).
"""

import argparse
import os
import sys

import yaml


def read_fasta(path):
    """Return (header, sequence) from a single-record FASTA."""
    if not os.path.exists(path):
        sys.exit(
            f"FASTA not found: {path}\n"
            "Download the CCTX2 sequence (UniProt A8NDT7) and put it there, or\n"
            "point target.fasta in the config somewhere else."
        )
    header, chunks = None, []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if header is not None:
                    break  # only the first record
                header = line[1:]
            else:
                chunks.append(line)
    seq = "".join(chunks).upper()
    if not seq:
        sys.exit(f"No sequence found in {path}")
    return header, seq


def slice_regions(seq, regions):
    """Concatenate 1-based inclusive regions of seq."""
    out = []
    for start, stop in regions:
        if start < 1 or stop > len(seq) or start > stop:
            sys.exit(
                f"Region [{start}, {stop}] is outside the sequence "
                f"(length {len(seq)}). Check the domain boundaries in the config."
            )
        out.append(seq[start - 1:stop])
    return "".join(out)


def build_yaml(seq, ligand_smiles=None, ligand_id="L"):
    """Boltz-2 input schema: one protein chain, optionally one ligand."""
    sequences = [{"protein": {"id": "A", "sequence": seq}}]
    if ligand_smiles:
        sequences.append({"ligand": {"id": ligand_id, "smiles": ligand_smiles}})
    doc = {"version": 1, "sequences": sequences}
    if ligand_smiles:
        # Ask for an affinity prediction as well as a pose.
        doc["properties"] = [{"affinity": {"binder": ligand_id}}]
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="inputs")
    ap.add_argument("--constructs", nargs="*", default=None,
                    help="subset of construct names; default is all")
    ap.add_argument("--ligands", nargs="*", default=None,
                    help="subset of ligand names, plus the literal 'none'; "
                         "default is 'none' only")
    args = ap.parse_args()

    with open(args.config) as fh:
        cfg = yaml.safe_load(fh)

    _, seq = read_fasta(cfg["target"]["fasta"])
    print(f"sequence length: {len(seq)}")

    constructs = cfg["constructs"]
    names = args.constructs or list(constructs)
    lig_names = args.ligands or ["none"]

    os.makedirs(args.out, exist_ok=True)
    written = []
    placeholder_hits = []

    for cname in names:
        if cname not in constructs:
            sys.exit(f"Unknown construct '{cname}'. Known: {list(constructs)}")
        sub = slice_regions(seq, constructs[cname]["regions"])

        for lname in lig_names:
            if lname == "none":
                doc = build_yaml(sub)
            else:
                ligs = cfg.get("ligands", {})
                if lname not in ligs:
                    sys.exit(f"Unknown ligand '{lname}'. Known: {list(ligs)}")
                smiles = ligs[lname]["smiles"]
                if "PLACEHOLDER" in smiles:
                    placeholder_hits.append(lname)
                doc = build_yaml(sub, smiles)

            stem = cname if lname == "none" else f"{cname}__{lname}"
            path = os.path.join(args.out, f"{stem}.yaml")
            with open(path, "w") as fh:
                yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
            written.append((path, len(sub), lname))

    print(f"\nwrote {len(written)} input file(s) to {args.out}/")
    for path, n, lig in written:
        print(f"   {os.path.basename(path):30s} {n:4d} aa   ligand: {lig}")

    if placeholder_hits:
        print("\nWARNING: these ligands still have PLACEHOLDER SMILES in the "
              "config and will fail or give nonsense:")
        for lname in sorted(set(placeholder_hits)):
            print(f"   {lname}")

    # Sanity reminder that is easy to forget.
    doms = cfg.get("domains", {})
    if doms and max(stop for _, stop in doms.values()) != len(seq):
        print("\nNOTE: the last domain boundary does not equal the sequence "
              "length. Check that the domain ranges in the config match the "
              "numbering of this FASTA.")


if __name__ == "__main__":
    main()
