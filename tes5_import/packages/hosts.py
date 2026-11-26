"""Quests that carry converted packages and never stop, and the armed state of a script-forced package.

See: docs/commentary/tes5_import_package.md#package-hosts
"""

import struct

from script_convert.constants import STAGE_PACKAGE_ALIAS_SCRIPT
from script_convert.pipeline import build_vmad_package_fragment, build_vmad_quest_fragments
from ..base.owned_records import adopted_or_derived, one_rank_faction
from ..base.writer import (pack_formid_subrecord, pack_record, pack_string_subrecord, pack_subrecord,
                           pack_uint32_subrecord)
from .custom_roots import in_faction_gate
from .greet_hold import HOLD_FRAGMENT

#: EditorID of the quest that hosts every actor's package alias.
HOST = 'TES4Packages'

#: Source quests one package alias can listen to by name; with more it listens to every stage set.
SOURCE_SLOTS = 8

#: FormLists TES4Polyfill.AddScriptPackage reads: forced packages, and each one's armed faction at the same index.
FORCED_PACKAGES, ARMED_FACTIONS = 'TES4ScriptPackageList', 'TES4ScriptPackageArmed'

#: The shared OnEnd fragment that disarms a forced package; its `Armed` property is the package's faction.
DISARM_SCRIPT = 'TES4_ScriptPackageDone'

#: QUST DNAM flags Start Game Enabled + Starts Enabled; priority just under the force-greet pool's.
_HOST_FLAGS, HOST_PRIORITY = 0x0011, 99


class PackageHosts:
    """The one quest that hosts this plugin's package aliases."""

    def __init__(self, writer, own_quests: dict):
        """`own_quests`: remapped quest FormID -> its export record."""
        self._own = own_quests
        self.host = writer.derive_formid('QUST', HOST)

    def of(self, quest_fid: int) -> int:
        """The host of `quest_fid`'s packages; a master's quest hosts its own."""
        return self.host if quest_fid in self._own else quest_fid


def forced_lists(writer, master_index) -> tuple:
    """({EditorID: FormID}, own) of the two FormLists; a master's pair is adopted."""
    found = {name: adopted_or_derived(writer, master_index, 'FLST', name)
             for name in (FORCED_PACKAGES, ARMED_FACTIONS)}
    return {name: fid for name, (fid, _) in found.items()}, all(own for _, own in found.values())


def create_armed_faction(writer, pack_fid: int) -> int:
    """Writes the hidden faction an actor is in while `pack_fid` is forced on it; returns its FormID."""
    name = 'TES4Armed_%06X' % (pack_fid & 0x00FFFFFF)
    fid = writer.derive_formid('FACT', name)
    writer.add_record('FACT', one_rank_faction(fid, name, 'Armed'))
    return fid


def armed_gate(faction: int, hold: bool = False) -> tuple:
    """(VMAD, CTDA) of a forced package: disarm on end, valid only while armed; `hold` = a force greet's exit hold."""
    vmad = pack_subrecord('VMAD', build_vmad_package_fragment(DISARM_SCRIPT, object_props={'Armed': faction},
                                                              on_begin=HOLD_FRAGMENT if hold else ''))
    return vmad, in_faction_gate(faction, 1.0)


def write_forced_lists(writer, fids: dict, armed: dict) -> None:
    """Writes the package list and the faction list, index for index (`armed`: package -> faction)."""
    order = sorted(armed)
    for name, members in ((FORCED_PACKAGES, order), (ARMED_FACTIONS, [armed[p] for p in order])):
        subs = pack_string_subrecord('EDID', name)
        subs += b''.join(pack_formid_subrecord('LNAM', fid) for fid in members)
        writer.add_record('FLST', pack_record('FLST', fids[name], 0, subs))


def _listeners(host_fid: int, sources: list) -> dict:
    """The alias script's properties: its source quests, or `Every` past the slots."""
    if len(sources) > SOURCE_SLOTS:
        return {'Every': host_fid}
    return {'Source%d' % (i + 1): fid for i, fid in enumerate(sources)}


def host_record(host_fid: int, alias_sources: dict, count: int, alias_subs: bytes) -> bytes:
    """The host QUST: EDID [VMAD] DNAM NEXT ANAM aliases.

    `alias_sources`: alias id -> the quests whose stages gate its packages.
    """
    subs = pack_string_subrecord('EDID', HOST)
    scripts = [(alias_id, [(STAGE_PACKAGE_ALIAS_SCRIPT, _listeners(host_fid, sources))])
               for alias_id, sources in sorted(alias_sources.items()) if sources]
    if scripts:
        subs += pack_subrecord('VMAD', build_vmad_quest_fragments(HOST, [], alias_scripts=scripts,
                                                                  quest_fid=host_fid))
    subs += pack_subrecord('DNAM', struct.pack('<HBBII', _HOST_FLAGS, HOST_PRIORITY, 0, 0, 0))
    subs += pack_subrecord('NEXT', b'')
    subs += pack_uint32_subrecord('ANAM', count) + alias_subs
    return pack_record('QUST', host_fid, 0, subs)
