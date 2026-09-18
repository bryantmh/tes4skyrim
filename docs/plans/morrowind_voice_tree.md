# Morrowind voice (`Sound\Vo`) conversion
Status: PLAN

Sound records, object sounds and creature sounds are
converted (see
[tes4_export_morrowind.md#which-sounds-a-plugin-ships](../commentary/tes4_export_morrowind.md#which-sounds-a-plugin-ships));
the voice tree is deliberately excluded and needs its own pass.

## Why it is not covered by the ownership rule

Every other sound reaches the game through a record: a `SOUN` names a file, and
`DOOR`/`LIGH`/`ACTI` name a `SOUN`. Ownership is therefore decidable per file --
the plugin that names it, minus whatever a master already names.

**Morrowind has no voice-type record.** There is no TES3 equivalent of `VTYP`,
and nothing on `INFO` names an audio file. The engine resolves a line's audio by
PATH CONVENTION from the INFO's own race and sex filters:

    Sound\Vo\<race>\<sex>\<code><linekind><NN>.mp3

Measured on the dev install: 7,024 files, 19 race dirs, each split `f`/`m`,
7,015 `.mp3`. `INFO` carries the filters that pick the folder (`RNAM` race on
4,756 records, plus `SNAM`/`ANAM`/`CNAM`), never a filename.

So the per-file ownership rule cannot see this tree at all -- no record
references it -- and `copy_loose_sounds` skips it via `_VOICE_DIR = 'vo'`.

## The one place a path IS authored

The `Say` opcode takes a literal path, and those 42 call sites in
Morrowind.esm point into this tree:

    Say "Vo\Misc\DA_AzuraIntro1.wav"
    Say "vo\misc\Dagoth Ur Taunt 1.mp3"

These are the only voice files any authored data names, so they are the cheapest
possible first slice: copy exactly what `SCPT`/`INFO` result scripts name.

## Options considered

1. **Scripted paths only** -- ~42 files. Authored indicator, no reverse
   engineering, but leaves all dialogue silent.
2. **Race dirs the plugin's NPCs use** -- copy `Vo\<race>\` for every race an
   `NPC_` record of this plugin references. Ownership-scoped without decoding
   filenames.
3. **Derive each INFO's paths from its filters** -- most precise, but needs the
   `<code>` prefix -> race mapping reverse engineered (`d` = Dark Elf, etc.).

## The Morroblivion wrinkle

In Morroblivion mode the two sources must MERGE, not substitute: Morroblivion
re-recorded only 411 voice files (`export/Morrowind_ob.esm/sound/voice/`,
Oblivion-style `<quest>_<editorid>_<n>.mp3` names) against vanilla's 7,024.
Morroblivion's line wins where it exists; vanilla fills the rest. Note the two
use disjoint naming schemes, so the merge is by INFO identity, not by filename.

## Downstream

`Say` also needs the runtime opcode itself, which needs a lookup table the
sidecar does not yet write -- no `MWSN.txt`, and `SOUN` is in neither
`_ITEM_EXPORTS` nor `_SCRIPTED_EXPORTS`. `PlaySound3D`/`GetSoundPlaying` name a
SOUN ID (45 call sites) and need that same table; `Say` names a path and needs
this tree.
