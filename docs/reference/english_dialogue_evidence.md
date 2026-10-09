# Evidence: missing English dialogue after conversion

These observations were extracted directly from the original English
Oblivion.esm/Knights.esp and the converted English plugins. File SHA-256 hashes
are included in `evidence.json`. No game launch or plugin conversion was needed.

## 1. Knights: prophet's speech loses its continuation

| Record in original Knights.esp | Original | Converted Knights.esp |
| --- | --- | --- |
| DIAL `01002CB8`, ND09ProphetSermon01 | Present | Present as `02002CB8` |
| DIAL `01002CD0`, ND09ProphetSermon02 | Present | Absent |
| DIAL `01002CCE`, ND09ProphetSermon03 | Present | Absent |
| INFO `01002CCF`, final response under Sermon03 | Present; result script `setstage ND09 25` | Absent; exact response text also absent |

The original continuation is recorded in the following INFO subrecords:

```text
INFO 01002CB9: TCLT payload d0 2c 00 01 -> DIAL 01002CD0 (Sermon02)
INFO 01002CD1: TCLT payload ce 2c 00 01 -> DIAL 01002CCE (Sermon03)
INFO 01002CCF: SCTX = setstage ND09 25
```

The final INFO's original file offset is `0x1F6648`. Its 20-byte record header:

```text
49 4e 46 4f 6d 01 00 00 00 00 00 00 cf 2c 00 01 04 2c 4b 00
```

This identifies an INFO record with FormID `01002CCF`; its body is uncompressed.
The response begins: "Go now, knights. Face the beast. Fear not".

## 2. A surviving response points to a missing topic

Original Knights INFO `01002A11`, under Oblivion's `INFOGENERAL` topic, has a
TCLT link to `01002BC7` (`NDRumorsUmaril`). Both records exist in the original.

The converted INFO survives as `02002A11` at file offset `0x4AA81B`. It still
contains TCLT payload `c7 2b 00 02`, pointing to `02002BC7`. That DIAL is absent
from the converted Knights plugin.

Across both English plugins, the original set has **0 unresolved TCLT links**;
the converted set has **86**, from **49 INFOs** to **11 omitted DIALs**.
`dangling-links.csv` lists every affected link using plugin name and local ID.
Both owner plugins were supplied to the check; there are **0 unindexed-owner
links** in either set.

## 3. Oblivion.esm also loses quest-bearing dialogue

Original Oblivion topic `000C9FB2` (`TG11TalkToMillona15`) contains INFO
`0007BA9E`. The INFO's result script includes:

```text
Set TG11Heist.TrackConversation to 15
SetStage TG11Heist 135
```

Both the original topic identity and INFO identity are absent from the
converted Oblivion plugin. The INFO's exact response text is absent throughout
the inspected converted set. Its original file offset is `0x1065FEAD`.

Record loss is established. Whether alternative code paths allow these quests
to finish has not been verified in gameplay.

## Reproduce independently

`verify.py` uses only Python's standard library and imports no converter code.
It reads records, master tables and continuation links directly from the
binaries and prints JSON. Supply the original and converted plugins in order:

```text
python verify.py --pair ORIGINAL/Oblivion.esm CONVERTED/Oblivion.esm --pair ORIGINAL/Knights.esp CONVERTED/Knights.esp
```

FormIDs shown above are the bytes stored in the inspected files. A plugin
editor can display different leading load-order digits. To locate the same
record, use its owning plugin and lower six digits, or its DIAL EditorID.
The verifier resolves ownership through each file's MAST table, accounting
for the additional Skyrim.esm master in the converted files.

`evidence.json` contains the observed hashes, record headers, byte offsets,
TCLT payloads and script text. `converted: null` means the record identity
was not found. Matching text elsewhere is checked separately; synthesized
replacement functionality would still require investigation.

The verifier is intended for these English TES4/TES5 plugins; source text is
decoded as CP1252 and converted text as UTF-8. It only reads input files and
prints its findings.
