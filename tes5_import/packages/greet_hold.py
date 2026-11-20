"""Opt-in exit hold of a conversation the source forced: a package OnBegin fragment that arms its speaker.

See: docs/commentary/tes_runtime_dialogue.md#no-exit-while-choices-are-offered
"""

from script_convert.pipeline import build_vmad_package_fragment
from ..base.writer import pack_subrecord

#: Static fragment script of a force greet with no OnEnd work; its Fragment_0 holds the exit.
HOLD_SCRIPT = 'TES4_ForceGreetHold'

#: OnBegin fragment that TES4_ForceGreetDone and TES4_ScriptPackageDone carry beside their OnEnd.
HOLD_FRAGMENT = 'Fragment_Hold'


def hold_vmad() -> bytes:
    """VMAD of a force greet with no other fragment: OnBegin holds the exit."""
    return pack_subrecord('VMAD', build_vmad_package_fragment(HOLD_SCRIPT, on_begin='Fragment_0', on_end=''))
