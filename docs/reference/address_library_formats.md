# Address Library database formats

**Code:** `tes_runtime/morrowind_runtime/plugin/addresses.cpp`,
`tes_runtime/plugin/addresses.cpp`, `game_bridge/plugin/addresses.cpp`,
`tools/disasm/address_lib.py`.

The Address Library maps SKSE **stable ids** to per-build RVAs, so one id table
serves every Skyrim build. Three on-disk formats exist, and the format number is
the first `i32` of the file. All are little-endian.

| Build | Filename | Format | Body |
|---|---|---|---|
| SE 1.5.x | `version-1-5-97-0.bin` | 1 | delta-coded |
| SE/AE 1.6.x | `versionlib-1-6-<build>-0.bin` | 2 | delta-coded |
| AE 1.7.x | `versionlib-1-7-<build>-0.bin` | 5 | flat `u32[]` |
| VR 1.4.15 | `version-1-4-15-0.csv` | CSV | `id,hex-rva` |

**The filename stem differs by era**: 1.5.x ships as `version-`, 1.6+ as
`versionlib-`. A loader that only tries `versionlib-` silently finds no database
on 1.5.97. The trailing number is the storefront (`0` Bethesda/Steam, `1` GOG,
`2` Epic), not a patch level; try the exact one, then fall back to `-0`.

## Formats 1 and 2 — delta-coded

Identical layout; format 1 is simply the older stamp. Header:

| Offset | Field |
|---|---|
| 0 | format (`1` or `2`) |
| 4 | version quad, 4 × `i32` |
| 20 | name length `i32`, then that many bytes (`"SkyrimSE.exe"`) |
| +0 | pointer size `i32` (8) |
| +4 | entry count `i32` |

Each entry is one control byte, low nibble for the id, high nibble for the
offset, both decoded against the previous entry's value:

| Kind | Meaning | Kind | Meaning |
|---|---|---|---|
| 0 | absolute `u64` | 4 | prev + `u16` |
| 1 | prev + 1 | 5 | prev − `u16` |
| 2 | prev + `u8` | 6 | absolute `u16` |
| 3 | prev − `u8` | 7 | absolute `u32` |

Kinds 6 and 7 are `u16`/`u32`. **Reading them as `u64` desyncs the whole
stream**, because every entry is delta-coded against the last.

Bit 3 of the high nibble (`0x80` in the control byte) is the pointer-size flag:
the *previous* offset is divided by `ptrSize` before the delta and the result
multiplied back after. It is not a plain "scale the delta" — that yields wrong
RVAs for the absolute kinds 0/6/7.

## Format 5 — flat array

1.7.x dropped delta coding entirely. The header also changed: the
length-prefixed name became a **fixed 64-byte NUL-padded field**, followed by
pointer size, a `u32` pad and the count.

| Offset | Field |
|---|---|
| 0 | format (`5`) |
| 4 | version quad, 4 × `i32` |
| 20 | name, fixed 64 bytes NUL-padded |
| 84 | pointer size `i32` (8) |
| 88 | pad `i32` (0) |
| 92 | count `u32` |
| 96 | `u32[count]` of RVAs, **indexed by stable id** |

So `rva = array[id]`, and `0` means the build does not cover that id. Id 0 and
an unmapped id are indistinguishable; both are treated as absent.

**Integrity check:** `filesize - 96 == count * 4` exactly. The delta formats'
equivalent is that the stream consumes the file to the last byte. Both are
mandatory — a partial parse yields plausible-but-wrong addresses, which are then
called as function pointers.

Measured (2026-09-20):

| Database | Format | Count |
|---|---|---|
| `version-1-5-97-0.bin` | 1 | 778,674 |
| `versionlib-1-6-659-0.bin` | 2 | 416,102 |
| `versionlib-1-6-1179-0.bin` | 2 | 428,510 |
| `versionlib-1-7-104-0.bin` | 5 | 565,759 (435,162 non-zero) |

## <a id="vr"></a>VR is not usable as an id source

VR 1.4.15 ships a CSV, and the ids we need are not in it. Measured against the
VR Address Library (Nexus 58101, 0.267.0): **14,284 ids**, versus 435,162 for
AE 1.7.104 — about 3% of the space.

Of `MorrowindRuntime`'s 91 ids and `TESRuntime`'s 44, **zero** appear in the VR
database. The gap is structural rather than incidental: the Papyrus-native
bands are almost entirely absent (`54000-55000`: 7 of 986; `55000-56000`: 24 of
998; `56000-57000`: 16 of 983), and those natives are nearly everything the
Morrowind runtime calls.

The VR ids that *do* exist share SE/AE numbering — 9,420 of 14,284 also appear
in the AE database — so a lookup looks valid while resolving nothing we need.
VR therefore resolves by **signature only**, as `TESRuntime`'s `ids.h` does with
`|`-separated prologue alternates.

## <a id="pre-ae-identity"></a>Pre-AE ids are PRESENT and WRONG

1.5.97 looks usable: it covers 90 of `MorrowindRuntime`'s 91 ids
(`kNpcVtable` 195816 is the exception) and 41 of `TESRuntime`'s 44. Presence is
not identity, and here the two come apart completely.

Measured with `stable_id_check --identity --identity-version 1.5.97` against
the unpacked 1.5.97 binary, resolving each `Native<>` id and comparing it with
the address that script's own Papyrus registration uses:

| Build | Correct | Wrong | Absent |
|---|---|---|---|
| 1.7.104 | **62** | 0 | 0 |
| 1.5.97 | **0** | **62** | 0 |

Every id resolves to a real function, and every one is the wrong function.
`ObjectReference.GetPositionX` (56178) gives `0x9aad70` where the native is
actually at `0x994240`.

The ids are not shifted by a constant, so no correction recovers them — the
delta between an AE id and the 1.5.97 id for the same function takes **26
distinct values** across 62 natives (−825, −824, −823, −821, −815, −812, −810,
−809, −800, −798, −796, −654 …). A rank comparison agrees: ids common to
1.6.659 and 1.6.1179 preserve their relative order 100% of the time, while
1.5.97 ↔ 1.6.659 manages 96.2%.

🛑 **`VersionDb::Load` therefore refuses any pre-AE runtime outright** and falls
back to signatures. A missing address is silent; a wrong one is called as a
function pointer. Supporting 1.5.x means signature alternates, exactly as VR
does — never the pre-AE database.
