#!/usr/bin/env python3
"""Read the one-letter sequence out of a structure file.

    python3 seq_from_structure.py reference/9s11.cif
    python3 seq_from_structure.py toolcheck/ref.cif --yaml lyz.yaml --id A

Taking the sequence from the structure, rather than from a separate FASTA,
guarantees that what gets predicted is what the reference actually contains.
A mismatch between the two is one of the easier ways to produce a confidently
wrong comparison: the numbering lines up, the scores come out poor, and
nothing says why.

Only residues with a CA atom are read, in file order, from one chain. Gaps in
the deposited model therefore vanish from the sequence, so compare the printed
residue range against the numbering before predicting: if the range is wider
than the count, residues are missing from the middle and the sequence is not
continuous.
"""

import argparse
import sys

THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    # Common modified residues, mapped to their parent.
    "MSE": "M", "SEC": "U", "PYL": "O", "HYP": "P", "CSO": "C",
    "PTR": "Y", "SEP": "S", "TPO": "T", "KCX": "K", "MLY": "K",
}


def residues_pdb(path, chain=None):
    for line in open(path):
        if line.startswith("ENDMDL"):
            break
        if not line.startswith("ATOM"):
            continue
        if line[12:16].strip() != "CA":
            continue
        if line[16] not in (" ", "A"):
            continue
        ch = line[21]
        if chain and ch != chain:
            continue
        yield ch, int(line[22:26]), line[17:20].strip().upper()


def residues_cif(path, chain=None):
    cols, rows, in_loop, past_header = [], [], False, False
    with open(path) as fh:
        for line in fh:
            s = line.strip()
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
                if rows:
                    break
                continue
            rows.append(s.split())

    def idx(*names):
        for n in names:
            if n in cols:
                return cols.index(n)
        return None

    i_atom = idx("label_atom_id", "auth_atom_id")
    i_comp = idx("label_comp_id", "auth_comp_id")
    i_seq = idx("auth_seq_id", "label_seq_id")
    i_ch = idx("auth_asym_id", "label_asym_id")
    i_model = idx("pdbx_PDB_model_num")
    i_alt = idx("label_alt_id")
    if None in (i_atom, i_comp, i_seq):
        sys.exit(f"{path}: atom_site loop lacks the columns needed")

    first_model = None
    for r in rows:
        if len(r) != len(cols):
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
        ch = r[i_ch] if i_ch is not None else "A"
        if chain and ch != chain:
            continue
        try:
            yield ch, int(r[i_seq]), r[i_comp].strip('"').upper()
        except ValueError:
            continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("structure")
    ap.add_argument("--chain", default=None,
                    help="which chain. Default: the one with most residues.")
    ap.add_argument("--yaml", default=None,
                    help="also write a Boltz input YAML to this path")
    ap.add_argument("--id", default="A", help="chain id inside the YAML")
    args = ap.parse_args()

    reader = (residues_pdb if args.structure.endswith((".pdb", ".ent"))
              else residues_cif)
    by_chain = {}
    for ch, num, comp in reader(args.structure, args.chain):
        by_chain.setdefault(ch, []).append((num, comp))
    if not by_chain:
        sys.exit(f"{args.structure}: no CA atoms found")

    chain = args.chain or max(by_chain, key=lambda c: len(by_chain[c]))
    res = by_chain[chain]
    seq = "".join(THREE_TO_ONE.get(c, "X") for _, c in res)
    nums = [n for n, _ in res]

    print(f"chain {chain}: {len(seq)} residues, numbered "
          f"{nums[0]}-{nums[-1]}")
    if len(by_chain) > 1:
        others = ", ".join(f"{c} ({len(v)})" for c, v in by_chain.items()
                           if c != chain)
        print(f"other chains present and ignored: {others}")

    span = nums[-1] - nums[0] + 1
    if span != len(seq):
        print(f"WARNING: the numbering spans {span} positions but only "
              f"{len(seq)} residues\n  are present, so {span - len(seq)} are "
              f"missing from the middle. The sequence\n  below is therefore "
              f"NOT continuous, and predicting it would join parts\n  that "
              f"are not adjacent. Check the gaps before using it.")
    unknown = seq.count("X")
    if unknown:
        bad = sorted({c for _, c in res if c not in THREE_TO_ONE})
        print(f"WARNING: {unknown} residue(s) could not be mapped: "
              f"{', '.join(bad)}")

    print(seq)

    if args.yaml:
        with open(args.yaml, "w") as fh:
            fh.write("version: 1\nsequences:\n  - protein:\n"
                     f"      id: {args.id}\n      sequence: {seq}\n")
        print(f"wrote {args.yaml}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
