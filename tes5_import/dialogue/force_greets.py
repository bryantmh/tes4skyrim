"""Oblivion's `StartConversation Player [<topic>]` as a Skyrim force greet.

Papyrus has no call that opens dialogue; Skyrim's mechanism is a ForceGreet
PACKAGE. One start-game quest owns a pool of aliases per topic, each alias
carrying a ForceGreet package that opens that topic. The converted call fills a
free alias of the pool with the speaker (`TES4Polyfill.ForceGreet`), and the
package's shared OnEnd fragment (`TES4_ForceGreetDone`) empties the alias once
the greeting has run -- the retire pattern of vanilla's
WITavernServerGreetPlayer.
"""

import struct

from script_convert.constants import FORCE_GREET_QUEST
from script_convert.pipeline import build_vmad_package_fragment
from ..base.master_export import values_of
from ..packages.converter import (ANY_TIME_PSDT, build_pkdt, dialogue_gate,
                                  FORCEGREET_INTERRUPT, force_greet_inputs,
                                  Inputs, package_markers, SPEED_RUN)
from ..record_types.common import (get_formid, get_int, get_str,
                                   pack_formid_subrecord, pack_record,
                                   pack_string_subrecord, pack_subrecord,
                                   pack_uint32_subrecord)
from .converter import classify_topic, should_skip_dial

#: The static package fragment script that empties a slot's alias (OnEnd).
DONE_SCRIPT = 'TES4_ForceGreetDone'

#: derive_formid sites; keys are the quest EditorID and (topic token, slot n).
_QUEST_SITE = 'FORCE_GREET_QUST'
_PACK_SITE = 'FORCE_GREET_PACK'

#: The Hello subtype that converted GREETING lines live under: StartConversation's default topic.
_HELLO = (1, struct.unpack('<I', b'HELO')[0])

#: StartGameEnabled | StartsEnabled, as the TES4PlayerScripts quest writes.
_SGE_FLAGS = 0x0011

#: Above a standing schedule's quest packages, so the greeting wins the alias stack.
_PRIORITY = 90

#: Optional | Allow Reuse in Quest: starts EMPTY, filled by ForceRefTo at runtime.
_ALIAS_FNAM = 0x0000000A


def dial_index(by_type: dict, master_export: dict = None) -> dict:
    """Lowercased DIAL EditorID -> record, the plugin's own over its masters'."""
    out = {}
    masters = values_of(master_export, 'DIAL')
    for rec in masters + by_type.get('DIAL', []):
        edid = get_str(rec, 'EditorID', '').lower()
        if edid:
            out[edid] = rec
    return out


def _topic_input(key: str, dials: dict) -> tuple:
    """The Topic input a pool opens: its DIAL, a bark's subtype, else Hello."""
    rec = dials.get(key)
    if rec is None or should_skip_dial(rec):
        return _HELLO
    _cat, _sub, snam, is_bark = classify_topic(get_str(rec, 'EditorID', ''),
                                               get_int(rec, 'DATA.Type'))
    if is_bark:
        return (1, struct.unpack('<I', snam)[0])
    return (0, get_formid(rec, 'FormID'))


def pool_package(edid: str, fid: int, alias_id: int, quest_fid: int,
                 inputs: Inputs, pkdt: bytes) -> bytes:
    """One pool slot's PACK: EDID VMAD PKDT PSDT [dialogue gate] QNAM PKCU <inputs> markers."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_subrecord('VMAD', build_vmad_package_fragment(
        DONE_SCRIPT, {'Slot': ('int', alias_id)}))
    subs += pack_subrecord('PKDT', pkdt)
    subs += pack_subrecord('PSDT', ANY_TIME_PSDT)
    subs += dialogue_gate(inputs.t)
    subs += pack_formid_subrecord('QNAM', quest_fid)
    subs += pack_subrecord('PKCU', struct.pack(
        '<III', len(inputs.t.inputs), inputs.t.formid, inputs.t.version))
    subs += inputs.emit()
    subs += package_markers()
    return pack_record('PACK', fid, 0, subs)


def _package(edid: str, fid: int, alias_id: int, quest_fid: int,
             topic: tuple) -> bytes:
    """One slot's ForceGreet PACK."""
    return pool_package(edid, fid, alias_id, quest_fid, force_greet_inputs(topic),
                        build_pkdt(0, SPEED_RUN, FORCEGREET_INTERRUPT))


def pool_quest(fid: int, edid: str, full: str, pack_fids: list) -> bytes:
    """The QUST owning one empty, runtime-filled alias per package."""
    return alias_quest(fid, edid, full, [pack_formid_subrecord('ALPC', p) for p in pack_fids])


def alias_quest(fid: int, edid: str, full: str, alias_bodies: list, vmad: bytes = b'') -> bytes:
    """A start-game QUST with one empty, runtime-filled alias per body (its subrecords after FNAM)."""
    subs = pack_string_subrecord('EDID', edid)
    if vmad:
        subs += pack_subrecord('VMAD', vmad)
    subs += pack_string_subrecord('FULL', full)
    subs += pack_subrecord('DNAM', struct.pack('<HBBII', _SGE_FLAGS,
                                               _PRIORITY, 0, 0, 0))
    subs += pack_subrecord('NEXT', b'')
    subs += pack_uint32_subrecord('ANAM', len(alias_bodies))
    for alias_id, body in enumerate(alias_bodies):
        subs += pack_uint32_subrecord('ALST', alias_id)
        subs += pack_string_subrecord('ALID', f'Slot{alias_id}')
        subs += pack_uint32_subrecord('FNAM', _ALIAS_FNAM)
        subs += body
        subs += pack_subrecord('ALED', b'')
    return pack_record('QUST', fid, 0, subs)


def write_force_greet_quest(writer, slots: dict, dials: dict) -> int:
    """Mint the quest and one ForceGreet PACK per alias of `slots`.

    `slots` is build_force_greet_slots' {topic token: (first alias, count)};
    alias ids are the packages' positions in its iteration order. Returns the
    quest FormID, or 0 when the plugin has no player-targeted StartConversation.
    """
    if not slots:
        return 0
    quest_fid = writer.derive_formid(_QUEST_SITE, FORCE_GREET_QUEST)
    pack_fids = []
    for key, (first, count) in slots.items():
        topic = _topic_input(key, dials)
        for n in range(count):
            fid = writer.derive_formid(_PACK_SITE, (key, n))
            edid = f'TES4ForceGreet_{key or "Greeting"}_{n}'
            writer.add_record('PACK', _package(edid, fid, first + n,
                                               quest_fid, topic))
            pack_fids.append(fid)
    writer.add_record('QUST', pool_quest(quest_fid, FORCE_GREET_QUEST,
                                         'TES4 Force Greets', pack_fids))
    return quest_fid
