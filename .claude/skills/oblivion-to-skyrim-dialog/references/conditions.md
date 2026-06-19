# CTDA Condition Conversion (TES4 → TES5)

Conditions gate every piece of dialogue, so faithful condition translation is
non-negotiable: the same checks must pass at the same times. Two changes are
needed — a **byte-layout re-pack** (24→32) and a **function-index
reconciliation** — plus FormID remapping and the voice/identity gates Skyrim
requires.

Everything here is derivable from the two function tables in
`references/xEdit/Core/wbDefinitionsTES4.pas` and `wbDefinitionsTES5.pas` (the
`(Index: N; Name: '…'; ParamType…)` lists). Where it isn't, it's flagged.

---

## Byte-layout re-pack (24 bytes → 32 bytes)

The two formats are field-compatible; Skyrim just adds trailing fields.

| Field | TES4 offset (24B) | TES5 offset (32B) | Transform |
|-------|-------------------|-------------------|-----------|
| Type (operator + flags) | byte 0 | byte 0 | Copy. Operator nibble and flag bits (Or 0x01, Run-on-target 0x02, Use Global 0x04) are the same. |
| Unused | 1–3 | 1–3 | Zero. |
| Comparison value (float / GLOB FormID) | 4–7 | 4–7 | Copy; remap if it's a GLOB FormID (Use Global flag). |
| Function index (U16) | 8–9 | 8–9 | Reconcile — see below. |
| Unused | 10–11 | 10–11 | Zero. |
| Param 1 | 12–15 | 12–15 | Copy; remap if it's a FormID. |
| Param 2 | 16–19 | 16–19 | Copy; remap if it's a FormID. |
| (TES4 trailing unused) | 20–23 | — | Drop. |
| Run On (U32) | — | 20–23 | New. Default 0 (Subject); set 1 (Target) if the TES4 "Run on target" flag was set, and clear that flag from byte 0 if Skyrim expects it as Run-On instead. **Judgment** — both encodings exist; keeping the flag bit also works. |
| Reference (FormID) | — | 24–27 | New. 0 unless Run On = Reference. |
| Unknown | — | 28–31 | `FF FF FF FF` (vanilla "none"). |

> Note `4–7` is a float by default and a GLOB FormID when the **Use Global**
> flag (byte0 bit 2, 0x04) is set — remap it as a FormID only in that case.

---

## Function-index reconciliation (the part that needs care)

Comparing the two games' function tables (197 TES4 entries vs 402 TES5 entries)
yields three cases. You can compute each case mechanically by joining the tables
on the numeric index:

### Case A — same index, same function (passthrough)

Most dialogue-critical functions are **identical** in index *and* parameter
types. Verified from the tables:

| Index | Function | Param types | TES4 | TES5 |
|-------|----------|-------------|------|------|
| 56 | GetQuestRunning | ptQuest | yes | yes |
| 58 | GetStage | ptQuest | yes | yes |
| 59 | GetStageDone | ptQuest, ptQuestStage | yes | yes |
| 72 | GetIsID | ptReferencableObject | yes | yes |

For these, copy the index unchanged and remap the FormID params. This covers the
overwhelming majority of quest/dialogue gating, so most conditions convert
cleanly.

### Case B — TES4 index has no TES5 entry (removed function) → DROP

If a TES4 function index does not appear in the TES5 table, the function was
removed; the condition cannot be evaluated and must be **dropped** (or the whole
INFO re-thought if the gate was essential). Example verifiable from the tables:

- **76 GetDisposition** — present in TES4, **absent in TES5** (disposition system
  removed). A line gated on disposition can't reproduce that gate; drop the
  condition. If disposition was the *only* thing making the line NPC-appropriate,
  add a `GetIsID`/relationship-based substitute. **Judgment** for the substitute.

General rule: build the set of TES5 indices; any TES4 condition whose index isn't
in that set is dropped (log it — a silently dropped gate changes behavior).

### Case C — same index, DIFFERENT function (reused slot) → DROP or REMAP

The dangerous case: an index exists in both tables but names a **different
function**. Leaving it unchanged silently invokes the wrong TES5 function.
Detect by joining on index and comparing the Name. Verified examples:

| Index | TES4 function | TES5 function at same index | Action |
|-------|---------------|------------------------------|--------|
| 224 | GetIsPlayerBirthsign | GetVATSMode | Drop (birthsigns gone; no equivalent). |
| 249 | GetPCFame | IsInDialogueWithPlayer | Drop (fame system gone; the TES5 function is unrelated and would mis-gate). |

If a TES4 function exists in TES5 but at a **different index** (same name, moved
slot), remap the index to the TES5 slot. Find these by joining the tables on
Name and comparing indices. (Verify each candidate against the tables before
remapping — don't assume.)

> Because index reuse is real, never copy a TES4 function index blindly. The
> correct, data-driven procedure is: (1) if the index maps to the same Name in
> TES5 → passthrough; (2) else if the Name exists at another TES5 index → remap;
> (3) else → drop. This is derivable entirely from the two function tables.

---

## FormID remapping

Both Param1, Param2, and the comparison value (when Use Global) can be FormIDs
referencing records (Quest, NPC, Cell, Global, etc. — see the parameter-type
table in the `oblivion-dialog-system` conditions reference). Each must be remapped
from the source plugin's load-order form to the output plugin's, and must point
at a record that **exists in the output** (e.g. a `GetStage(Q)` must reference the
converted Q; a `GetIsID(npc)` the converted NPC). A dangling FormID makes the
condition fail or error.

---

## Required injected conditions (Skyrim-specific, no TES4 source)

Skyrim needs gates Oblivion didn't, because its visibility model is
conditions-only. Inject these **before** the translated TES4 conditions so their
OR-chains stay isolated:

1. **Voice-type gate** — `GetIsVoiceType(VTYP)` (index 426; TES5-only, no TES4
   equivalent), OR-chained for multiple voices. Derived from the line's NPC(s) →
   VTYP (see `voice.md`). Reproduces Oblivion's implicit race+gender voice
   routing.
2. **Identity gate** — for conversation lines that were NPC-specific in Oblivion
   (or only AddTopic'd for certain NPCs), `GetIsID(npc)` so the line isn't
   offered to everyone once the owning quest runs (see `dial-info.md` "AddTopic →
   conditions").

---

## OR-chain mechanics (unchanged between games, but easy to break)

Both games OR-chain with bit 0 (0x01) of the type byte: set OR on every condition
in the chain **except the last**. When you *inject* voice/identity OR-chains,
keep them contiguous and put them first, so a trailing OR flag from a translated
TES4 condition can't leak into the injected chain and vice-versa. A leaked OR
flag turns an AND gate into "or true" and breaks the whole condition set.

---

## Fidelity summary for conditions

| Aspect | Faithful from data alone? | Notes |
|--------|---------------------------|-------|
| Byte re-pack 24→32 | Yes | Field-compatible. |
| Core dialogue/quest gates (56/58/59/72…) | Yes | Identical index + params. |
| Removed functions (e.g. GetDisposition) | Partially | Must drop; substitute is judgment. |
| Reused-index functions (e.g. 249, 224) | Yes (detect + drop/remap) | Derivable from the tables; never copy blindly. |
| FormID params | Yes | Remap; ensure target exists. |
| Voice/identity gates | n/a (injected) | Required to reproduce TES4 behavior in TES5's model. |
