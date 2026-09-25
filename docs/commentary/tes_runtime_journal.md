# TESRuntime: journal stage text

**Code:** `tes_runtime/tes/journal_objectives.cpp`, `tes_runtime/tes/journal_log.cpp`,
`asset_convert/ui/journal_patch.py`, `asset_convert/ui/avm1.py`,
`core/gui/journal.py` (Build > Quest Journal Stage Text).

## <a id="journal-stage-text"></a>Journal stage text: a clicked objective shows its stage's text

**Confirmed in game with Quest Journal Overhaul** while it lived in
MorrowindRuntime; the vanilla and SkyUI `QuestsPage` patch is not yet played,
and the move into TESRuntime is not yet played either.

Skyrim's journal shows ONE description per quest. Clicking one of a quest's
objectives now swaps it for the text the quest had when that objective first
appeared; clicking it again brings back the current text. It works on any
quest, not only converted ones, which is why it lives in TESRuntime rather than
MorrowindRuntime.

### What the engine keeps

Read off the journal's objective builder (`0x98a6a0` on 1.6.1170, `0x92b5f0`
on 1.6.659, same offsets in both):

- The description is NOT stored text. Each quest instance's record (array at
  `TESQuest+0x38`, count `+0x48`) holds a **(stage, log entry) pair** at
  `+0x38`/`+0x3a`, overwritten at every stage that has a log entry.
  `0x392ab0(record, quest, BSString*)` turns a pair into text: `0x3d1c70`
  fetches that stage's entry, `0x392c80` fills in alias names. It reads only
  the record's instance id, stage and entry, so the runtime calls it on a
  zeroed 0x40-byte stand-in record for any pair it saved.
- A stage number alone is not enough. In vanilla Skyrim.esm 700 stages have one
  texted log entry and 26 stages in 18 quests (MQ102, DA16, MS13, CWObj ...)
  have 2-9 condition-picked alternatives, so the pair is recorded whole.
- Objectives come from the **player's** objective array (`PlayerCharacter+0x588`,
  count `+0x598`, 16 bytes each: objective, instance, state), appended in
  display order. The journal walks it **newest first** and makes one row per
  entry that is: state 1, 3 or 5; for a Miscellaneous-type quest
  (`TESQuest+0xdf == 6`) state 1 only; of the selected quest and instance; and
  not flagged ORed (`objective+0x20 & 1`), whose text joins the next row.
  `ObjectiveAtRow` is that rule, headless-tested in `journal_log_test.cpp`.
- A row the movie receives carries formID, instance, status flags, target and
  text -- **no objective number**. Row position is the only exact identity, so
  the runtime rebuilds the same row list to map a clicked row back.

### Recording

TESRuntime polls the player's array every 33 ms on the game's main thread
(`StartMainThreadTick`, the same timer the jails use), **paused or not**
(dialogue shows objectives with the game held), once the player stands in a
cell, and does nothing unless the array's count or newest entry changed. A new
(objective, instance) records the quest instance's current pair under (quest
FormID, objective index, instance), saved in TESRuntime's co-save record `JRNL`
with FormIDs re-resolved on load.

The first poll after a load or new game only **learns** the array: objectives
already shown were shown under text nobody recorded, so they fall back to the
current description. Two stages inside one 33 ms poll credit both objectives
to the later stage's text. Saves made while this lived in MorrowindRuntime kept
their records under its co-save (`MWJL`); TESRuntime does not read those, so
those objectives also fall back to the current description.

### The movies

The journal lists dispatch clicks by NAME -- `addEventListener("itemPress",
this, "onObjectiveListSelect")` -- so the patch rewrites no existing bytecode.
It appends one DoAction to frame 1, after every class's DoInitAction, that
replaces class methods on the prototype:

| Movie | Class | Replaced |
|---|---|---|
| Vanilla / SkyUI `quest_journal.swf` | `QuestsPage` | `onObjectiveListSelect` (outside the Miscellaneous view it did nothing; the original still runs when there is no text, keeping Misc's set-active toggle), and `SetDescriptionText` so any movie-driven reset clears the shown row |
| Quest Journal Overhaul `questjournal.swf` | `QuestJournal` | `SetObjectives`, which then gives each row clip `"objective" + index` an `onPress` (its rows took no clicks at all) |

### <a id="journal-movies-reach-the-runtime"></a>🛑 How a movie reaches the runtime: NOT `skse.plugins`

SKSE adds `_global.skse` (and `skse.plugins.<name>`) from a hook INSIDE the
engine's `GFxLoader::LoadMovie`, at `+0x1dd` (`0xfb02ed` on 1.6.1170).
Quest Journal Overhaul does not use that function: it loads `questjournal`
through CommonLibSSE's `LoadMovieEx` (its DLL carries the `GFxMovieDef*`
lambda in `QuestMenu`'s constructor), which reimplements the load, so its
movie never gets `skse` at all. The first build called
`skse.plugins.MorrowindRuntime.GetObjectiveLog`; in game every call was a
silent no-op and nothing changed on screen.

So the runtime equips movies itself. Each poll it walks MenuManager's open
menus (`+0x110`, count `+0x120`, 8-byte `IMenu*`; view at `IMenu+0x10`) and
gives any movie whose root carries `TESRT_Patched` an object at
`_root.TESRT_Runtime` holding `GetObjectiveLog` and `JournalTrace`, whoever
loaded it. A movie patched while this lived in MorrowindRuntime carries the old
`MWRT_` names and is never equipped; rebuilding the Quest Journal Stage Text
mod replaces it (the patch's own marker string is unchanged, so the rebuild
replaces the old patch rather than stacking a second one).

Also measured: `LoadMovie` calls `CreateInstance` with `initFirstFrame = 1`
(`0xfb02c8`) BEFORE that hook, so a movie's frame-1 actions run before any
`skse` object exists even for engine-loaded movies. Frame-1 code may REPLACE
methods, but must not call the runtime.

QJO also ships a rebuilt SkyUI `quest_journal.swf` whose Quests tab closes the
journal and opens QJO's own menu, so its `QuestsPage` is never seen; patching
it too is harmless. The patcher recognises each class by its constant-pool
strings, finds the copy the game loads (loose file, else the archive whose
plugin loads last), and zips the result into `Finished Mods` as a mod that
must load after the UI mod it patches. The game folder is never written, so
uninstalling the mod is the undo. Patching replaces an earlier copy of the
patch, so reading the mod's own deployed output still works, but it keeps the
journal it was first built from: a QJO or SkyUI update needs the mod disabled
while it is rebuilt. QJO's rows are mouse-only; its movie gives them no
gamepad focus.
