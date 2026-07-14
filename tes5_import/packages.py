"""AI package wiring for converted actors.

TES4 PACK records are now really converted (see pack_converter.py), so an actor
keeps its OWN packages: the PKID list is the TES4 AIPackage list, in TES4 order,
because Skyrim — like Oblivion — runs the first package whose conditions pass.
Order is behaviour, not decoration.

Two things still come from vanilla:

* DPLT — the default package LIST every vanilla actor carries underneath its
  own packages (the fallback that keeps an actor doing *something* when none of
  its own packages apply).
* Creatures — creature AI is driven by the generated behaviour graph, not by
  TES4 packages (see docs/creature_conversion.md), and every vanilla creature
  carries exactly one package: DefaultMasterPackageCreature.  Keep that.

Quest packages are NOT in the actor's PKID list: they hang off a QUST reference
alias (ALPC), which is how they outrank the standing schedule.  See pack_aliases.
"""

from .text_reader import get_int

# Vanilla Skyrim.esm records (master index 0 — written unremapped)
PKID_CREATURE_MASTER = 0x0010F2A5   # PACK DefaultMasterPackageCreature
DPLT_CREATURE_LIST = 0x0010F2A6     # FLST DefaultMasterPackageListCreature
PKID_NPC_SANDBOX = 0x000BFB6B       # PACK DefaultSandboxCurrentLocation1024
DPLT_NPC_LIST = 0x00021E81          # FLST DefaultMasterPackageList
CSTY_DEFAULT = 0x0000003D           # CSTY DefaultCombatstyle
CSTY_ANIMAL = 0x00057BE8            # CSTY csWolf (vanilla wolf/dog ZNAM)

# fid_low24 -> TES4 PKDT.Type, built once per import run (Phase 0g)
_PACK_TYPES = {}

# Packages that belong to a quest and therefore live on a QUST alias (ALPC)
# instead of the actor's PKID list.  Populated from the PackagePlan.
_QUEST_PACKAGES = set()


def load_package_types(by_type: dict) -> None:
    """Phase 0g: index the TES4 PACK records by PKDT.Type."""
    from .text_reader import get_formid
    _PACK_TYPES.clear()
    for rec in by_type.get('PACK', []):
        try:
            fid = get_formid(rec, 'FormID') & 0x00FFFFFF
        except ValueError:
            continue
        _PACK_TYPES[fid] = get_int(rec, 'PKDT.Type', -1)
    print(f'  Package types: {len(_PACK_TYPES)} TES4 packages indexed')


def set_quest_packages(pack_fids) -> None:
    """Register the packages that are attached via QUST aliases."""
    _QUEST_PACKAGES.clear()
    _QUEST_PACKAGES.update(f & 0x00FFFFFF for f in pack_fids)


def npc_packages(pack_fids) -> list:
    """The PKID list for a converted NPC: its own packages, in TES4 order.

    Quest packages are filtered out — they reach the actor through the quest's
    reference alias (ALPC).  Leaving them here as well would let a quest package
    run outside its quest.
    """
    return [f for f in pack_fids
            if f and (f & 0x00FFFFFF) not in _QUEST_PACKAGES]
