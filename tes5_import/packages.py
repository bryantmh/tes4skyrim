"""Vanilla-package substitution for TES4 AI packages.

TES4 PACK records are skipped (SKIP_TYPES — TES5 packages are procedure
template trees with no direct binary conversion), so converted actors used
to keep PKID references to records that don't exist in the output. An actor
whose package list resolves to nothing gets no AI decisions at all: the
engine never sends the behavior graph any movement/attack events and the
actor stands in its idle state forever (the 2026-07-09 stuck-in-idle root
cause — behavior graph, records, and animation caches all verified clean,
and `sae moveStart` on a spawned dog worked while the AI layer stayed
silent, because the AI layer had no package content to act on).

Substitution mirrors vanilla Skyrim:

* Creatures: PKID DefaultMasterPackageCreature + DPLT
  DefaultMasterPackageListCreature — exactly what every vanilla creature
  (EncWolf/dog/skeever) carries as its ONLY package.
* Humanoid NPCs: TES4 wander/eat/sleep/find-style packages collapse into one
  DefaultSandboxCurrentLocation1024 (sandbox covers eat/sleep/idle
  procedures); DPLT DefaultMasterPackageList (the list 1,916 vanilla NPCs
  use). Ref-targeted TES4 types (follow/escort/accompany/use-item-at/
  ambush/flee) have no generic vanilla stand-in and are dropped — full PACK
  conversion is future work.
"""

from .text_reader import get_int

# Vanilla Skyrim.esm records (master index 0 — written unremapped)
PKID_CREATURE_MASTER = 0x0010F2A5   # PACK DefaultMasterPackageCreature
DPLT_CREATURE_LIST = 0x0010F2A6     # FLST DefaultMasterPackageListCreature
PKID_NPC_SANDBOX = 0x000BFB6B       # PACK DefaultSandboxCurrentLocation1024
DPLT_NPC_LIST = 0x00021E81          # FLST DefaultMasterPackageList
CSTY_DEFAULT = 0x0000003D           # CSTY DefaultCombatstyle
CSTY_ANIMAL = 0x00057BE8            # CSTY csWolf (vanilla wolf/dog ZNAM)

# TES4 PKDT.Type values that target a specific ref/location we can't infer
# (wbDefinitionsTES4: 1 Follow, 2 Escort, 7 Accompany, 8 UseItemAt,
# 9 Ambush, 10 FleeNotCombat) — no generic vanilla substitute exists.
_UNSUBSTITUTABLE_TYPES = {1, 2, 7, 8, 9, 10}

# fid_low24 → TES4 PKDT.Type, built once per import run (Phase 0g)
_PACK_TYPES = {}


def load_package_types(by_type: dict) -> None:
    """Phase 0g: index the exported (but skipped) TES4 PACK records so the
    actor converters can classify each AIPackage reference by PKDT.Type."""
    _PACK_TYPES.clear()
    for rec in by_type.get('PACK', []):
        try:
            fid = int(rec.get('FormID', '0'), 16) & 0x00FFFFFF
        except ValueError:
            continue
        _PACK_TYPES[fid] = get_int(rec, 'PKDT.Type', -1)
    print(f'  Package substitution: {len(_PACK_TYPES)} TES4 packages indexed')


def substitute_npc_packages(pack_fids) -> list:
    """Vanilla PKID FormIDs standing in for a TES4 NPC's package list.

    Any wander/eat/sleep/travel-style package becomes ONE sandbox package
    (packages referencing masters outside this export are unknown and count
    as wander — better alive than inert); ref-targeted types are dropped.
    """
    for pfid in pack_fids:
        ptype = _PACK_TYPES.get(pfid & 0x00FFFFFF, 5)
        if ptype not in _UNSUBSTITUTABLE_TYPES:
            return [PKID_NPC_SANDBOX]
    return []
