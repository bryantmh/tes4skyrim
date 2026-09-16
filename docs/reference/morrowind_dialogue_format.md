# Morrowind dialogue export format

The `KEY=VALUE` vocabulary `tes4_export/record_types/morrowind_dialog.py` emits
for TES3 `DIAL` and `INFO`, and `MorrowindRuntime.dll` reads back.

**This is not the TES4 vocabulary.** Every other Morrowind exporter translates
into TES4 field names so the existing `tes5_import` converters can read them.
Dialogue does not: nothing converts these into TES5 `DIAL`/`INFO` records. They
are consumed by OpenMW's own filter running inside the runtime DLL, so the dump
keeps TES3's field names, its string IDs and its condition encoding.

## <a id="signatures"></a>The signatures are `MWDI` / `MWIN`, not `DIAL` / `INFO`

🛑 These records are written under **`MWDI`** (topic) and **`MWIN`** (response),
deliberately not the TES3 signatures they come from.

`DIAL` and `INFO` already name TES4 records that `tes5_import` converts into
TES5 dialogue, and every record entering that pipeline must carry a **hex
FormID**. These carry a TES3 **string id** instead, because the runtime resolves
them by name and never needs a FormID at all. Exporting them as `DIAL`/`INFO`
puts them in `all_records`, where `_reserve_formid_space` reaches
`int(rec['FormID'], 16)` and the import dies on the first topic
(`invalid literal for int() with base 16: 'HH_WinCamonna'`).

So the signature is what keeps runtime data out of the record pipeline. The
files are `MWDI.txt` and `MWIN.txt`.

## <a id="dial"></a>DIAL — a topic

| Key | Value |
|---|---|
| `EditorID` | the TES3 string id, which is also the displayed topic text |
| `DialType` | `Topic`, `Voice`, `Greeting`, `Persuasion` or `Journal` |

`DialType` is `DATA`'s first byte: `0 Topic, 1 Voice, 2 Greeting,
3 Persuasion, 4 Journal`.

## <a id="info"></a>INFO — one response

| Key | Value |
|---|---|
| `EditorID` | the INFO's own id (`INAM`) |
| `Topic` | the `DIAL` this INFO belongs to |
| `Ordinal` | **0-based position in the topic's list** |
| `InfoType` | the owning topic's kind, from `DATA` |
| `Disposition` | disposition threshold (`DATA`, field 2) |
| `JournalIndex` | the same field, read as a journal index when the topic is a `Journal` |
| `Rank` / `PCRank` | speaker and player faction rank, `-1` when unset |
| `Gender` | `0` male, `1` female, `-1` either |
| `Actor` `Race` `Class` `Faction` `Cell` `PCFaction` | string-id filters (`ONAM` `RNAM` `CNAM` `FNAM` `ANAM` `DNAM`) |
| `FactionLess` | `1` when `FNAM` is the sentinel `FFFF` — "speaker has NO faction" |
| `Voice` | `SNAM`, a path under `Sound/Vo/` |
| `Response` | `NAME`, the displayed text |
| `ResultScript` | `BNAM`, **uncompiled MWScript source** |
| `QuestStatus` | `Name`, `Finished` or `Restart` (`QSTN`/`QSTF`/`QSTR`) |
| `Condition[n].*` | see below |
| `ConditionCount` | how many conditions were emitted |

### <a id="inam-not-name"></a>🛑 An INFO's id is `INAM`, not `NAME`

`tes3_reader` fills `Tes3Record.record_id` from the `NAME` subrecord, which is
right for every other TES3 record. On an INFO, `NAME` is **the response text**
and the identity is `INAM`. Reading `record_id` here yields a 512-character
paragraph as the EditorID — it looks plausible in a dump and is wrong. Use
`info_id()`.

### <a id="info-identity"></a>🛑 INFO identity is `(Topic, EditorID)`, not `EditorID`

`INAM` is **not unique across the file.** Measured over `Morrowind.esm`: **99
ids are reused, covering 211 extra records**, and **all 99 span different
topics — none repeats inside one topic.** Bethesda copied INFOs between sibling
topics and kept their ids; the five `Sanguine *` topics carry the same five
INAMs, and `attack on a guar hide trader` / `Attack on guar hide trader` are a
case-variant pair sharing four.

This is authored source data, not an export defect. Anything keying INFOs must
key on the pair, and a reader that builds a bare `{INAM: info}` map silently
loses 211 responses.

### <a id="ordinal"></a>`Ordinal` is the filter precedence

Morrowind takes the **first matching INFO in list order**, so the order is
semantic, not cosmetic. The source chains it through `PNAM`/`NNAM` (previous and
next), but a reader that walked that chain would have to trust every link in a
23,693-record list. The ordinal is therefore emitted explicitly and is the
authority.

### <a id="conditions"></a>`Condition[n]` — the SCVR rule

A TES3 condition is an ASCII rule string plus a value subrecord. The rule is at
least 5 characters and the parts are positional:

| Part | Meaning |
|---|---|
| `Rule` | the raw string, kept so nothing is lost to our own parse |
| `Index` | `rule[0]`, `'0'`–`'9'` — which of several same-function slots |
| `Function` | `rule[1]` — `1` a numbered function, `2` global, `3` local, `4` journal, `5` item, `6` dead, `7` not-id, `8` not-faction, `9` not-class, `A` not-race, `B` not-cell, `C` not-local |
| `Comparison` | `rule[4]` — `0` `=`, `1` `!=`, `2` `>`, `3` `>=`, `4` `<`, `5` `<=` |

Then **exactly one of two shapes**, by `Function`:

| `Function` | Emits | From |
|---|---|---|
| `1` (numbered) | `FunctionIndex` | `rule[2:4]`, two digits, `00`–`73` |
| anything else | `VarType` + `Variable` | `rule[2]`, then `rule[5:]` |

> 🛑 **The two shapes overlap in the same bytes.** A numbered function's index
> occupies `rule[2:4]` — precisely where a variable rule keeps its type
> character and its `X` padding. Slicing both alike parses `01500` (function
> 50, `Choice`, compared `=`) as `VarType='5'`, `Comparison='0'` and an empty
> variable: no error, no missing field, and every `Choice` condition silently
> unreadable. The first export made exactly this mistake and the corpus check
> caught it by reporting **0 `Choice` conditions where 1,748 were measured**.

> 🛑 **`ValueType` must be carried, never collapsed.** The value arrives as an
> `INTV` (int32) or `FLTV` (float32) subrecord and the comparison differs.
> Measured over `Morrowind.esm`: 24,819 `INTV` against 16 `FLTV` — rare enough
> to look like noise, common enough to be wrong 16 times.

## <a id="measured"></a>Measured over the vanilla files

| | Morrowind.esm | Tribunal.esm | Bloodmoon.esm |
|---|---:|---:|---:|
| DIAL | 2,358 | 893 | 860 |
| of which Journal | 632 | 620 | 629 |
| INFO | 23,693 | — | — |
| INFO under a Journal topic | 2,489 | 2,257 | 2,397 |
| `QSTN` / `QSTF` / `QSTR` | **0 / 0 / 0** | 615 / 813 / 4 | 623 / 831 / 3 |

`Morrowind.esm` DIAL kinds: 1,698 Topic, 632 Journal, 10 Greeting,
10 Persuasion, 8 Voice.

Other counts that shape the runtime's work: **24,835 conditions**, **12,799
result scripts**, **4,513 voiced lines** (of 23,693 — Morrowind is essentially
unvoiced), longest response 512 characters, 48 responses and 3,195 result
scripts containing newlines.

> 🛑 **Vanilla Morrowind has NO quest-status flags.** They arrived with
> Tribunal's quest system. A vanilla journal quest is a bare list of indexed
> entries with no completion marker, so anything downstream that wants "is this
> quest done" cannot read it off the record for `Morrowind.esm` content.

## <a id="which-conditions-matter"></a>Which conditions content actually uses

Rule kind `1` is "a numbered function", whose index sits at `rule[2:4]`; every
other kind is a variable lookup. Counting the numbered ones across
`Morrowind.esm` and `TR_Mainland.esm`: **54 of the 74 functions are used, 20
never are.**

| | TR_Mainland | Morrowind |
|---|---:|---:|
| numbered conditions | 14,856 | 5,486 |
| distinct functions | 47 | 39 |
| **`Choice`** | **11,244 (76%)** | 1,748 (32%) |
| `TalkedToPc` | 962 | 601 |
| `PcExpelled` | 721 | 405 |
| `SameFaction` | 614 | 514 |

> 🛑 **`Choice` dominates.** It is three quarters of TR_Mainland's numbered
> conditions, because it is not a filter in the ordinary sense — it is how a
> branching conversation re-enters the INFO scan after the player picks an
> option. Getting it wrong does not break one filter, it breaks branching
> dialogue generally.

**Never used in either file** — every one of these can be a logged stub without
affecting the test corpus:

```
PcMagicka PcFatigue PcBlock PcMediumArmor PcHeavyArmor PcLongBlade
PcAxe PcSpear PcAthletics PcEnchant PcUnarmored PcLightArmor
PcShortBlade PcMarksman PcHandToHand PcWillpower PcSpeed PcEndurance
Werewolf PcWerewolfKills
```

That list is most of the Morrowind-only skill set, which is why mapping skills
rather than modelling them costs so little in practice: the skills with no
Skyrim counterpart are the ones dialogue never asks about. The ones content
does use — `PcSpeechcraft`, `PcMercantile`, `PcAlchemy`, `PcConjuration`,
`PcIntelligence` — either map directly or are attributes taking the passing
stub.

## <a id="verified"></a>Verified against the source

`python convert.py -f Morrowind.esm --export-only` (19s), counted against a
direct read of `Morrowind.esm`:

| | source | export |
|---|---:|---:|
| DIAL | 2,358 | 2,358 |
| INFO | 23,693 | 23,693 |
| conditions | 24,835 | 24,835 |
| result scripts | 12,799 | 12,799 |
| voiced lines | 4,513 | 4,513 |
| float conditions | 16 | 16 |

Also checked: 0 INFOs whose `Topic` names no DIAL, 0 ordinals out of sequence,
0 ids over 40 characters (the [INAM-vs-NAME](#inam-not-name) trap leaking
response text). Largest topic is `Hello` with 3,260 responses — worth knowing
before anything builds a per-topic list eagerly.
