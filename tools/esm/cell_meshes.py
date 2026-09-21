"""List meshes referenced by cells in a TES4 export directory.

Walks REFR/ACHR/ACRE records for the given cell(s), resolves base records
(NAME FormID) across all export files, and prints each base's model path(s).

Usage:
    python tools/esm/cell_meshes.py <export_dir> --cell <FormID_or_EditorID> [--cell ...]
    python tools/esm/cell_meshes.py export/Oblivion.esm --cell AnvilMagesGuild --cell 00007966
    python tools/esm/cell_meshes.py export/Oblivion.esm --cell AnvilMagesGuild --intersect AnvilCastlePrivateQuarters

Options:
    --intersect X   Also list the second cell and print the intersection of mesh sets.
    --meshes-only   Print only unique mesh paths (one per line), no base record info.
"""
import argparse
import os
import re
from collections import defaultdict

PLACED_FILES = ("REFR.txt", "ACHR.txt", "ACRE.txt")


def iter_records(path):
    """Yield dicts of KEY->list-of-values per record (fast line scan)."""
    if not os.path.isfile(path):
        return
    rec = None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line == "---RECORD_BEGIN---":
                rec = defaultdict(list)
            elif line == "---RECORD_END---":
                if rec is not None:
                    yield rec
                rec = None
            elif rec is not None and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                rec[k].append(v)


def resolve_cell_fid(export_dir, ident):
    """Accept an 8-hex FormID or a CELL EditorID; return the FormID string."""
    if re.fullmatch(r"[0-9A-Fa-f]{8}", ident):
        return ident.upper()
    for rec in iter_records(os.path.join(export_dir, "CELL.txt")):
        if rec.get("EditorID", [""])[0].lower() == ident.lower():
            return rec["FormID"][0].upper()
    raise SystemExit(f"CELL with EditorID {ident!r} not found")


def collect_cell_bases(export_dir, cell_fids):
    """Return {cell_fid: {base_fid: count}} from all placed-object files."""
    out = {fid: defaultdict(int) for fid in cell_fids}
    for fname in PLACED_FILES:
        for rec in iter_records(os.path.join(export_dir, fname)):   # noqa: plugin-path (record .txt)
            parent = rec.get("ParentCELL", [None])[0]
            if parent and parent.upper() in out:
                base = rec.get("NAME", [None])[0]
                if base:
                    out[parent.upper()][base.upper()] += 1
    return out


def read_masters(export_dir):
    """Master plugin names of an export, in load order, from its _HEADER.txt."""
    masters = []
    header = os.path.join(export_dir, "_HEADER.txt")
    if os.path.isfile(header):
        with open(header, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Master["):
                    masters.append(line.split("=", 1)[1].strip())
    return masters


def build_master_aware_index(export_dir, wanted_fids):
    """build_base_index over the plugin AND its masters, keyed by the plugin's own FormIDs.

    A base whose index byte names master k is looked up in export/<master k>
    under that master's own index byte (its master count).
    """
    masters = read_masters(export_dir)
    index = build_base_index(export_dir, wanted_fids)
    for k, master in enumerate(masters):
        master_dir = os.path.join(os.path.dirname(os.path.abspath(export_dir)), master)
        if not os.path.isdir(master_dir):
            continue
        own = len(read_masters(master_dir))
        remap = {"%02X%s" % (own, fid[2:]): fid
                 for fid in wanted_fids if int(fid[:2], 16) == k}
        for mfid, entry in build_base_index(master_dir, set(remap)).items():
            index[remap[mfid]] = entry
    return index


def build_base_index(export_dir, wanted_fids):
    """Scan every export file once; return {fid: (signature, edid, [model paths])}."""
    index = {}
    for fname in sorted(os.listdir(export_dir)):
        if not fname.endswith(".txt") or fname in PLACED_FILES or fname == "CELL.txt":
            continue
        path = os.path.join(export_dir, fname)   # noqa: plugin-path (record .txt)
        for rec in iter_records(path):
            fid = rec.get("FormID", [None])[0]
            if not fid or fid.upper() not in wanted_fids:
                continue
            models = []
            for k, vals in rec.items():
                if k.endswith("MODL") or k.endswith(".MODL") or ".Model" in k:
                    models.extend(vals)
            index[fid.upper()] = (
                rec.get("Signature", [fname[:-4]])[0],
                rec.get("EditorID", [""])[0],
                models,
            )
    return index


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export_dir")
    ap.add_argument("--cell", action="append", required=True, help="CELL FormID (8 hex) or EditorID; repeatable")
    ap.add_argument("--meshes-only", action="store_true")
    args = ap.parse_args()

    cell_fids = [resolve_cell_fid(args.export_dir, c) for c in args.cell]
    bases_by_cell = collect_cell_bases(args.export_dir, cell_fids)
    all_bases = set()
    for d in bases_by_cell.values():
        all_bases.update(d)
    index = build_master_aware_index(args.export_dir, all_bases)

    mesh_sets = {}
    for label, fid in zip(args.cell, cell_fids):
        bases = bases_by_cell[fid]
        meshes = set()
        if not args.meshes_only:
            print(f"\n=== CELL {label} ({fid}): {sum(bases.values())} placed refs, {len(bases)} unique bases ===")
        for bfid in sorted(bases):
            sig, edid, models = index.get(bfid, ("????", "<unresolved>", []))
            meshes.update(m.lower() for m in models)
            if not args.meshes_only:
                mstr = "; ".join(models) if models else "-"
                print(f"  {bfid} {sig:5} x{bases[bfid]:<3} {edid:35} {mstr}")
        mesh_sets[label] = meshes
        if args.meshes_only:
            for m in sorted(meshes):
                print(m)
        else:
            print(f"  -> {len(meshes)} unique mesh paths")

    if len(cell_fids) > 1 and not args.meshes_only:
        inter = set.intersection(*mesh_sets.values())
        print(f"\n=== INTERSECTION across {len(mesh_sets)} cells: {len(inter)} meshes ===")
        for m in sorted(inter):
            print(f"  {m}")


if __name__ == "__main__":
    main()
