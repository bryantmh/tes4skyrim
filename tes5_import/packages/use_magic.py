"""TES4 UseItemAt whose item is a spell: Skyrim's UseMagic at the same place, cast as many times.

See: docs/commentary/tes5_import_package.md#use-item-at-with-a-spell
"""
from ..base.text_reader import get_formid, get_int
from .templates import USE_MAGIC

#: USE_MAGIC's two integer inputs: the fewest and the most casts before the procedure completes.
CASTS_MIN, CASTS_MAX = 8, 9

#: Signature the package indexes give a spell base.
SPELL_SIG = 'SPEL'


def spell_item_inputs(pick, make_inputs):
    """UseMagic inputs for `pick` when its Object-ID item is a spell, else None.

    `pick` is the converter's _Pick; `make_inputs` builds its Inputs for a template.
    """
    spell = get_formid(pick.rec, 'PTDT.Target')
    if get_int(pick.rec, 'PTDT.Type', -1) != 1 or pick.ctx.sig_of_base(spell) != SPELL_SIG:
        return None
    casts = max(1, get_int(pick.rec, 'PTDT.Count', 0) or 0)
    inputs = make_inputs(USE_MAGIC)
    inputs.set('location', pick.loc)
    inputs.set('target', pick.tgt)
    inputs.set_slot(CASTS_MIN, casts)
    inputs.set_slot(CASTS_MAX, casts)
    return inputs
