#!/usr/bin/env python3
"""Check config.yaml against the reference structure it describes.

    python3 check_reference.py --config config.yaml

Every residue number in config.yaml is an assertion about the deposited
model, and all of them are easy to get wrong in a way nothing complains
about. The paper numbers CCTX2 from 1; the deposition adds a His8 tag and a
TEV site, so PDB numbering is 16 higher. A domain boundary off by 16 still
produces results: the comparison runs, the scores come out, and the numbers
are confidently about the wrong residues.

This checks what can be checked mechanically:

  - the reference is readable and which chain to use
  - every domain boundary exists in the model
  - each domain is contiguous, or how many residues are missing from it
  - the excluded regions lie inside the modelled range
  - the residues declared unmodelled really are absent, and nothing else is

It cannot tell you the boundaries are the right ones. For that, read
Appendix Table S4 and add 16.
"""

import argparse
import os
import sys

try:
    import yaml
except ImportError:
    sys.exit("pyyaml not installed. Run 'source env.sh' first.")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare import read_ca, excluded_residues          # noqa: E402

PROBLEMS = []
NOTES = []


def bad(msg):
    PROBLEMS.append(msg)
    print(f"   FAIL  {msg}")


def ok(msg):
    print(f"   ok    {msg}")


def note(msg):
    NOTES.append(msg)
    print(f"   note  {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--reference", default=None,
                    help="override the path in the config")
    args = ap.parse_args()

    with open(args.config) as fh:
        cfg = yaml.safe_load(fh)

    target = cfg.get("target", {})
    path = args.reference or target.get("reference_pdb")
    chain = target.get("reference_chain")
    if not path:
        sys.exit("No reference_pdb in the config and none given")
    if not os.path.exists(path):
        sys.exit(f"{path} not found. Run ./get_reference.sh first.")

    nums, xyz, _ = read_ca(path, chain)
    if xyz is None or not len(xyz):
        sys.exit(f"Could not read CA atoms from {path} "
                 f"(chain {chain!r}). Check reference_chain.")
    present = {int(n) for n in nums}
    lo, hi = min(present), max(present)
    ok(f"{path}: chain {chain}, {len(present)} residues, numbered {lo}-{hi}")
    if len(present) != hi - lo + 1:
        note(f"{hi - lo + 1 - len(present)} residue numbers between {lo} and "
             f"{hi} are absent from the model")

    # --- domains ---------------------------------------------------------
    doms = cfg.get("domains", {})
    if not doms:
        note("no domains defined, so nothing to check against")
    for name, (start, stop) in sorted(doms.items(), key=lambda kv: kv[1][0]):
        start, stop = int(start), int(stop)
        missing_ends = [r for r in (start, stop) if r not in present]
        if missing_ends:
            bad(f"domain {name} boundary {missing_ends} not in the model "
                f"(model covers {lo}-{hi})")
            # The commonest cause, so say it rather than leave it to be found.
            shifted = [r + 16 for r in (start, stop)]
            if all(r in present for r in shifted):
                bad(f"   but {shifted} are both present: {name} looks like "
                    f"paper numbering. Add 16.")
            continue
        inside = sum(1 for r in range(start, stop + 1) if r in present)
        span = stop - start + 1
        if inside == span:
            ok(f"domain {name}: {start}-{stop}, {span} residues, contiguous")
        else:
            note(f"domain {name}: {start}-{stop}, {inside} of {span} residues "
                 f"present; {span - inside} missing from the middle")

    # A config shifted wholesale by 16 mostly still "fits", because the
    # paper's numbers land inside the deposited range too. The tell is at the
    # ends: the domains should reach both ends of the modelled chain, give or
    # take a short linker. A systematic shift leaves a matching amount
    # uncovered at one end and overshooting at the other.
    if doms:
        first = min(int(a) for a, _ in doms.values())
        last = max(int(b) for _, b in doms.values())
        head = first - lo        # residues modelled before the first domain
        tail = hi - last        # residues modelled after the last domain

        if head < 0 or tail < 0:
            # A domain reaches outside the modelled chain. The per-domain
            # checks above have already failed; this names the likely shift.
            bad(f"the domains cover {first}-{last}, which reaches outside "
                f"the modelled chain {lo}-{hi}")
            # A config shifted down by k shows head = -k and tail = +k.
            if head < 0 < tail and abs(head + tail) <= 2:
                bad(f"   head and tail are {head} and {tail}, the signature "
                    f"of every boundary being {tail} too low. Add {tail} "
                    f"throughout: that is the His8 tag plus TEV site, which "
                    f"the paper does not number.")
            elif tail < 0 < head and abs(head + tail) <= 2:
                bad(f"   head and tail are {head} and {tail}: every boundary "
                    f"looks {head} too high. Subtract {head} throughout.")
        elif head >= 8 or tail >= 8:
            # Not an error. Real termini can lie outside every domain.
            note(f"domains cover {first}-{last} of the modelled {lo}-{hi}, so "
                 f"{head} residues at the start and {tail} at the end belong "
                 f"to no domain. Fine if those are genuine termini; check "
                 f"against Appendix Table S4 if you did not expect them.")
        else:
            ok(f"domains span {first}-{last}, reaching both ends of the "
               f"modelled chain ({lo}-{hi})")

    # Overlaps and gaps between domains are worth seeing, not errors.
    ordered = sorted(((int(a), int(b), n) for n, (a, b) in doms.items()))
    for (a1, b1, n1), (a2, b2, n2) in zip(ordered, ordered[1:]):
        if a2 <= b1:
            bad(f"domains {n1} and {n2} overlap at {a2}-{b1}")
        elif a2 > b1 + 1:
            note(f"{a2 - b1 - 1} residues between {n1} and {n2} "
                 f"({b1 + 1}-{a2 - 1}) belong to no domain")

    # --- excluded regions ------------------------------------------------
    skip = excluded_residues(cfg)
    if skip:
        outside = sorted(r for r in skip if r not in present)
        if outside:
            bad(f"{len(outside)} excluded residues are not in the model at "
                f"all, first few: {outside[:6]}. Excluding residues that are "
                f"already absent hides a numbering error rather than fixing "
                f"one.")
        else:
            ok(f"{len(skip)} excluded residues, all present in the model")
        in_dom = {}
        for r in skip:
            for name, (a, b) in doms.items():
                if int(a) <= r <= int(b):
                    in_dom[name] = in_dom.get(name, 0) + 1
        if in_dom:
            per = ", ".join(f"{k} {v}" for k, v in sorted(in_dom.items()))
            note(f"excluded residues per domain: {per}")
            for name, (a, b) in doms.items():
                span = int(b) - int(a) + 1
                frac = in_dom.get(name, 0) / span
                if frac > 0.25:
                    note(f"{frac:.0%} of domain {name} is excluded. Report "
                         f"that domain's score with and without the list.")

    # --- residues declared unmodelled ------------------------------------
    un = cfg.get("unmodelled_in_reference")
    if un:
        a, b = int(un[0]), int(un[1])
        wrongly_present = [r for r in range(a, b + 1) if r in present]
        if wrongly_present:
            bad(f"unmodelled_in_reference says {a}-{b} are absent, but "
                f"{len(wrongly_present)} of them are in the model, first few: "
                f"{wrongly_present[:6]}")
        else:
            ok(f"unmodelled_in_reference {a}-{b}: confirmed absent")
        if b + 1 != lo and a <= lo:
            note(f"the model starts at {lo}, so the declared unmodelled range "
                 f"ending at {b} does not meet it. Check whether residues "
                 f"{b + 1}-{lo - 1} are also missing.")

    # --- constructs ------------------------------------------------------
    for name, spec in (cfg.get("constructs") or {}).items():
        regions = spec.get("regions") or []
        for a, b in regions:
            a, b = int(a), int(b)
            covered = sum(1 for r in range(a, b + 1) if r in present)
            if covered == 0:
                note(f"construct {name} region {a}-{b} has no counterpart in "
                     f"the reference. Expected for a predicted-only state.")
            elif covered < (b - a + 1):
                note(f"construct {name} region {a}-{b}: {covered} of "
                     f"{b - a + 1} residues exist in the reference")

    print()
    if PROBLEMS:
        print(f"   {len(PROBLEMS)} problem(s). Fix config.yaml before running "
              f"anything whose numbers you intend to report.")
        return 1
    print("   config.yaml is consistent with the reference.")
    print("   This does not say the boundaries are correct, only that they "
          "exist and\n   fit. For correctness, check them against Appendix "
          "Table S4 plus 16.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
