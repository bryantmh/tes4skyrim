"""Short objective (NNAM) text for converted quests.

Skyrim renders TWO quest strings, from two different subrecords:

  * CNAM -- the quest log entry. Long retrospective first-person prose that
    accumulates in the journal. Vanilla Skyrim.esm: mean 170 chars.
  * NNAM -- the objective line on the HUD / quest tracker. A short
    second-person imperative. Vanilla Skyrim.esm: mean 31 chars, median 27.

Measured across 1441 vanilla objectives with strings resolved from
`Skyrim - Interface.bsa`, NOT ONE of the 1265 distinct NNAM texts is reused as
a CNAM: Bethesda authors them as separate things.

TES4 has no second string. Every Oblivion QUST stage carries exactly one text
field (`Stage[].Log[].Text`) -- confirmed by enumerating every field across all
390 Oblivion QUST records -- because Oblivion's journal had no objective HUD to
feed. So NNAM was receiving the full log paragraph: 95% of converted objectives
ran past vanilla's p90 of 48 chars.

This module supplies the missing short form from a curated table.

THE 71-CHARACTER CAP IS AN ENGINE LIMIT, NOT A STYLE TARGET. The objective
field does not wrap: characters past the limit are simply not rendered, in
every language. 71 is the longest token-free objective vanilla ships (DA03,
"Give the Rueful Axe to Clavicus Vile OR kill Barbas with the Rueful Axe").
Vanilla strings longer than that are only long because they carry unexpanded
<Alias=>/<Global=> tokens the engine substitutes at runtime; converted text has
no tokens, so stored length IS rendered length.

The table is keyed on the SOURCE TEXT rather than plugin+EditorID+stage, so
identical journal text resolves to one entry wherever it appears and no
per-plugin bookkeeping is needed. Keys are normalized with `_key()` -- the same
whitespace collapse the table was built with.

Coverage is Oblivion.esm, the official Oblivion DLCs (Knights.esp,
DLCBattlehornCastle, DLCFrostcrag, DLCHorseArmor, DLCMehrunesRazor, DLCOrrery,
DLCThievesDen and DLCVileLair -- DLCSpellTomes ships no QUST, and
DLCShiveringIsles.esp is a zero-byte stub), Morrowind_ob.esm, Nehrim.esm
(German) and Translation.esp (the English Nehrim strings). Anything not in the
table falls back to the long text, which is exactly the previous behaviour, so
an unknown plugin degrades to the status quo rather than to something worse.
"""
import json
import os

#: Set by load_objective_text(); empty until then, so lookups fall back to long.
_SHORT = {}

#: The curated (EditorID, stage) -> short line table. See generated/README.md.
DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'generated', 'objective_short_text.json')

#: The curated short lines for TES3 journal pages with no exact key in DATA_PATH.
TES3_DATA_PATH = os.path.join(os.path.dirname(DATA_PATH),
                              'objective_short_text_tes3.json')

#: The objective field renders at most this many characters.
OBJECTIVE_MAX_CHARS = 71


def _key(text: str) -> str:
    """Normalize a source journal string to its table key."""
    return ' '.join((text or '').split())


def _table_entries(path: str) -> dict:
    """`{normalized source text: short line}` from one table, {} if absent."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as fh:
        entries = json.load(fh)['entries']
    return {_key(k): v for k, v in entries.items()
            if isinstance(v, str) and v.strip()}


def load_objective_text(quiet: bool = False) -> int:
    """Load the curated table and the TES3 one. Returns the entry count.

    Safe to call repeatedly; import_main calls it once and worker pools inherit
    the populated dict through the fork/initializer path.
    """
    global _SHORT
    _SHORT = {**_table_entries(TES3_DATA_PATH), **_table_entries(DATA_PATH)}
    if not quiet:
        print(f"  Objective text: {len(_SHORT)} short objective lines")
    return len(_SHORT)


def short_objective(text: str) -> str:
    """The short objective line for one TES4 journal string.

    Falls back to `text` unchanged when the table has no entry, so a plugin the
    table was not built for keeps the old behaviour.

    The cap is enforced here rather than trusted from the data file: a
    hand-edited table must not be able to push an over-long string into NNAM,
    where the tail would be silently invisible in game.
    """
    if not text:
        return text
    short = _SHORT.get(_key(text))
    if not short:
        return text
    short = short.strip()
    if len(short) > OBJECTIVE_MAX_CHARS:
        short = short[:OBJECTIVE_MAX_CHARS].rstrip()
    return short
