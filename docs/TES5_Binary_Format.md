# Skyrim SE (TES5/SSE) Binary File Format — Exact Layout

All information sourced from xEdit source code (`external/xEdit/Core/`).

---

## 1. Record Header (Main Record) — 24 bytes (SSE/TES5)

**Source**: `wbImplementation.pas` line 1022 (`TwbMainRecordStruct`), `wbDefinitionsTES5.pas` line 4446 (`wbSizeOfMainRecordStruct := 24`), `wbDefinitionsCommon.pas` line 1240 (`wbRecordHeader`)

```
Offset  Size  Type      Field                   Notes
------  ----  --------  ----------------------  ------------------------------------
0x00    4     char[4]   Signature               Record type: 'NPC_', 'STAT', etc.
0x04    4     uint32    DataSize                Byte size of record data AFTER header
0x08    4     uint32    Flags                   Record flags (see below)
0x0C    4     uint32    FormID                  Load-order FormID
0x10    4     uint32    VCS1                    Version Control Info 1 (timestamp)
0x14    2     uint16    FormVersion             43=Skyrim LE, 44=Skyrim SE
0x16    2     uint16    VCS2                    Version Control Info 2
------  ----  --------  ----------------------  
Total:  24 bytes (0x18)
```

**Note**: TES4 (Oblivion) records are 20 bytes — they lack the FormVersion and VCS2 fields (`wbSizeOfMainRecordStruct := 20` in `wbDefinitionsTES4.pas`). TES3 (Morrowind) is 16 bytes.

The packed record in Delphi (`TwbMainRecordStruct`):
```pascal
TwbMainRecordStruct = packed record
  mrsSignature : TwbSignature;    // 4 bytes (array[0..3] of AnsiChar)
  mrsDataSize  : Cardinal;        // 4 bytes
  _Flags       : TwbMainRecordStructFlags;  // 4 bytes (Cardinal)
  _FormID      : TwbFormID;       // 4 bytes (Cardinal)
  _VCS1        : Cardinal;        // 4 bytes
  _Version     : Word;            // 2 bytes (Form Version)
  _VCS2        : Word;            // 2 bytes
end;  // = 24 bytes
```

### Record Flags (at offset 0x08)

| Bit     | Hex          | Flag                    |
|---------|-------------|-------------------------|
| 0       | `0x00000001` | ESM                    |
| 5       | `0x00000020` | Deleted                |
| 6       | `0x00000040` | Has LOD tree / Constant (context-dependent) |
| 7       | `0x00000080` | Localized (on TES4 header record) |
| 8       | `0x00000100` | ESL (Starfield only)   |
| 9       | `0x00000200` | ESL (SSE) / Cast Shadows |
| 10      | `0x00000400` | Persistent             |
| 11      | `0x00000800` | Initially Disabled     |
| 12      | `0x00001000` | Ignored                |
| 14      | `0x00004000` | Partial Form           |
| 15      | `0x00008000` | Visible When Distant   |
| 17      | `0x00020000` | Dangerous / Off Limits |
| 18      | `0x00040000` | **Compressed**         |
| 19      | `0x00080000` | Can't Wait             |

### Form Version Values (set at record creation)

| Game                   | FormVersion |
|------------------------|------------|
| Skyrim SE (gmSSE)      | **44**     |
| Skyrim LE (gmTES5)     | 43         |
| Fallout 4              | 131        |
| Fallout 76             | 184        |
| Starfield              | 555        |
| Fallout 3 / FNV        | 15         |

Source: `wbImplementation.pas` lines 9223–9230.

---

## 2. Group (GRUP) Header — 24 bytes

**Source**: `wbImplementation.pas` line 1832 (`TwbGroupRecordStruct`)

```
Offset  Size  Type      Field        Notes
------  ----  --------  -----------  ----------------------------------------
0x00    4     char[4]   Signature    Always 'GRUP'
0x04    4     uint32    GroupSize    TOTAL size including this 24-byte header
0x08    4     uint32    Label        Meaning depends on GroupType (see below)
0x0C    4     int32     GroupType    0–10 (see table)
0x10    4     uint32    Stamp        Timestamp
0x14    4     uint32    Unknown      (padding/VCS)
------  ----  --------  -----------
Total:  24 bytes (0x18)
```

```pascal
TwbGroupRecordStruct = packed record
  grsSignature : TwbSignature;   // 4 bytes, always 'GRUP'
  grsGroupSize : Cardinal;       // 4 bytes, total group size INCLUDING header
  grsLabel     : Cardinal;       // 4 bytes, meaning varies by type
  grsGroupType : Integer;        // 4 bytes
  grsStamp     : Cardinal;       // 4 bytes
  grsUnknown   : Cardinal;       // 4 bytes
end;
```

### Group Types and Label Interpretation

| Type | Name                                | Label contains                             |
|------|-------------------------------------|--------------------------------------------|
| 0    | Top-level                           | 4-char record signature (e.g. 'NPC_')      |
| 1    | World Children                      | FormID of parent WRLD record               |
| 2    | Interior Cell Block                 | Block number (int32)                       |
| 3    | Interior Cell Sub-Block             | Sub-block number (int32)                   |
| 4    | Exterior Cell Block                 | Grid Y (int16 hi) + Grid X (int16 lo)     |
| 5    | Exterior Cell Sub-Block             | Grid Y (int16 hi) + Grid X (int16 lo)     |
| 6    | Cell Children                       | FormID of parent CELL record               |
| 7    | Topic Children                      | FormID of parent DIAL record               |
| 8    | Cell Persistent Children            | FormID of parent CELL record               |
| 9    | Cell Temporary Children             | FormID of parent CELL record               |
| 10   | Cell Visible Distant Children       | FormID of parent CELL record               |

### CELL Group Hierarchy

**Interior Cells**:
```
GRUP (type 0, label='CELL')              Top-level CELL group
  GRUP (type 2, label=blockNum)          Interior Cell Block
    GRUP (type 3, label=subBlockNum)     Interior Cell Sub-Block
      CELL record                        The cell itself
      GRUP (type 6, label=cellFormID)    Cell Children
        GRUP (type 8, label=cellFormID)  Persistent Children (REFRs, ACHRs)
        GRUP (type 9, label=cellFormID)  Temporary Children (REFRs, ACHRs)
```

**Worldspaces/Exterior Cells**:
```
GRUP (type 0, label='WRLD')              Top-level WRLD group
  WRLD record
  GRUP (type 1, label=wrldFormID)        World Children
    CELL record                          Persistent worldspace cell
    GRUP (type 6, label=cellFormID)      Cell Children of persistent cell
      GRUP (type 8, label=cellFormID)    Persistent Children
      GRUP (type 9, label=cellFormID)    Temporary Children
    GRUP (type 4, label=gridXY)          Exterior Cell Block
      GRUP (type 5, label=gridXY)        Exterior Cell Sub-Block
        CELL record                      Exterior cell at grid coords
        GRUP (type 6, label=cellFormID)  Cell Children
          GRUP (type 8, label=cellFormID)  Persistent Children
          GRUP (type 9, label=cellFormID)  Temporary Children
          GRUP (type 10, label=cellFormID) Visible Distant Children (LOD)
```

### DIAL/INFO Hierarchy
```
GRUP (type 0, label='DIAL')              Top-level DIAL group
  DIAL record
  GRUP (type 7, label=dialFormID)        Topic Children
    INFO record
    INFO record
    ...
```

---

## 3. Subrecord Header — 6 bytes (TES4/TES5/SSE)

**Source**: `wbImplementation.pas` line 1418 (`TwbSubRecordHeaderStruct`)

```
Offset  Size  Type      Field        Notes
------  ----  --------  -----------  ----------------------------------
0x00    4     char[4]   Signature    Subrecord type: 'EDID', 'FULL', 'OBND', etc.
0x04    2     uint16    DataSize     Byte size of subrecord data following header
------  ----  --------  -----------
Total:  6 bytes
```

```pascal
TwbSubRecordHeaderStruct = packed record
  srsSignature : TwbSignature;        // 4 bytes
  // Variant: for TES3 = Cardinal (4B), for TES4+ = Word (2B)
  case Integer of
    0: (_DataSizeCardinal : Cardinal); // TES3
    1: (_DataSizeWord     : Word);     // TES4/TES5/SSE
end;
```

**SizeOf** (from `TwbSubRecordHeaderStruct.SizeOf`):
- TES3 (Morrowind): 4 + 4 = **8 bytes**  
- TES4+ (Oblivion/Skyrim/SSE): 4 + 2 = **6 bytes**

### XXXX Subrecord (Oversized Data)

When a subrecord's data exceeds 65535 bytes (max for uint16), xEdit uses the **XXXX** protocol:
1. Write a `XXXX` subrecord header (signature='XXXX', size=4)
2. Write the actual data size as a uint32 (4 bytes of data)
3. Write the real subrecord header with size=**0**
4. Write the actual data (length from XXXX's uint32)

On read: if a subrecord has `DataSize == 0`, check the previous subrecord. If it was `XXXX`, use its uint32 payload as the real data size.

---

## 4. TES4 File Header Record

**Source**: `wbDefinitionsTES5.pas` line 12764, `wbDefinitionsCommon.pas` line 420

The very first record in any ESP/ESM/ESL file. Signature is always `TES4` (not `TES5`!).

### Header Record Flags (specific to TES4 record)

| Bit | Hex          | Flag               |
|-----|-------------|---------------------|
| 0   | `0x00000001` | ESM                 |
| 1   | `0x00000002` | Altered             |
| 2   | `0x00000004` | Checked             |
| 3   | `0x00000008` | Active              |
| 4   | `0x00000010` | Optimized File      |
| 5   | `0x00000020` | Temp ID Owner       |
| 7   | `0x00000080` | **Localized**       |
| 8   | `0x00000100` | Precalc Data Only   |
| 9   | `0x00000200` | ESL (SSE only)      |

### HEDR Subrecord — 12 bytes

```
Offset  Size  Type      Field              Notes
------  ----  --------  -----------------  --------------------------
0x00    4     float32   Version            1.7 for Skyrim LE, 1.71 for SSE
0x04    4     uint32    Number of Records  Total record count in file
0x08    4     uint32    Next Object ID     Next available FormID
------  ----  --------  -----------------
Total:  12 bytes
```

**HEDR Version values** (from `wbDefinitionsTES5.pas` line 13643):
- Skyrim LE (`gmTES5`): `1.7`
- Skyrim SE (`gmSSE`): **`1.71`**
- Oblivion (`gmTES4`): `1.0` (for comparison)

### MAST + DATA Subrecords (Master File Entries)

Each master file dependency is represented by a MAST/DATA pair:

```
MAST subrecord:
  Header:  'MAST' + uint16(size)
  Data:    null-terminated string (filename, e.g. "Skyrim.esm\0")

DATA subrecord:
  Header:  'DATA' + uint16(8)
  Data:    8 bytes (always zero, historically file size but unused)
```

Multiple MAST+DATA pairs can appear, one per master.

### Other TES4 Subrecords

| Signature | Content                     | Required |
|-----------|-----------------------------|----------|
| HEDR      | Header (version, count, ID) | Yes      |
| CNAM      | Author (string)             | Optional |
| SNAM      | Description (string)        | Optional |
| MAST+DATA | Master file entries          | Optional |
| ONAM      | Overridden Forms (FormID[]) | Optional |
| SCRN      | Screenshot (raw bytes)       | Optional |
| INTV      | Unknown                     | Optional |
| INCC      | Interior Cell Count (uint32) | Optional |

### Complete TES4 Record Example (Binary)
```
Bytes      Meaning
--------   ---------------------------
54 45 53 34   Signature: 'TES4'
XX XX XX XX   DataSize (uint32, size of all subrecords)
01 00 00 00   Flags (0x01 = ESM)
00 00 00 00   FormID (always 0 for header)
00 00 00 00   VCS1
2C 00         FormVersion (44 = 0x2C for SSE)
00 00         VCS2
-- subrecords follow --
48 45 44 52   'HEDR'
0C 00         DataSize: 12
B8 1E DB 3F   Version: 1.71 as float (≈ 0x3FDB1EB8)
XX XX XX XX   Number of Records
XX XX XX XX   Next Object ID
4D 41 53 54   'MAST'
XX XX         DataSize (string length + null)
...           "Skyrim.esm\0"
44 41 54 41   'DATA'
08 00         DataSize: 8
00 00 00 00   (8 zero bytes)
00 00 00 00
```

---

## 5. OBND (Object Bounds) — 12 bytes data

**Source**: `wbDefinitionsCommon.pas` line 1407

```
Offset  Size  Type    Field    Notes
------  ----  ------  ------   ----------------
0x00    2     int16   X1       Min X bound
0x02    2     int16   Y1       Min Y bound
0x04    2     int16   Z1       Min Z bound
0x06    2     int16   X2       Max X bound
0x08    2     int16   Y2       Max Y bound
0x0A    2     int16   Z2       Max Z bound
------  ----  ------  ------
Total:  12 bytes
```

As a subrecord, the full binary is:
```
4F 42 4E 44   'OBND'
0C 00         DataSize: 12
XX XX         X1 (int16, little-endian)
XX XX         Y1
XX XX         Z1
XX XX         X2
XX XX         Y2
XX XX         Z2
```

**Required on**: ACTI, ALCH, AMMO, ARMO, BOOK, CONT, DOOR, ENCH, FLOR, FURN, GRAS, INGR, KEYM, LIGH, LSCR, MISC, NPC_, SCRL, SLGM, SNDR, SPEL, STAT, TREE, WEAP — essentially all "placeable" records.

---

## 6. Compressed Records

**Source**: `wbImplementation.pas` line 9373 (`DecompressIfNeeded`), flag at `wbInterface.pas` line 20488

When record flag bit 18 (`0x00040000`) is set, the record's data is **zlib-compressed**:

```
Record Header (24 bytes, flags has 0x00040000 set)
  DataSize field = size of compressed payload + 4
  [4 bytes]  uint32  UncompressedLength    Original decompressed size
  [N bytes]  zlib    CompressedData        zlib compressed data (N = DataSize - 4)
```

Decompression:
1. Read `DataSize` from record header
2. First 4 bytes of data = `UncompressedLength` (uint32)
3. Remaining `DataSize - 4` bytes = zlib compressed stream
4. Decompress to `UncompressedLength` bytes → these are the actual subrecords

---

## 7. Complete File Layout

```
TES4 record (file header, 24-byte record header + subrecords)
GRUP (type 0, label='GMST')    Top-level group
  GMST record
  GMST record
  ...
GRUP (type 0, label='KYWD')
  KYWD record
  ...
GRUP (type 0, label='NPC_')
  NPC_ record
  ...
GRUP (type 0, label='CELL')    Special hierarchy (see §2)
  GRUP (type 2, ...)           Interior Cell Blocks
    ...
GRUP (type 0, label='WRLD')   Special hierarchy (see §2)
  WRLD record
  GRUP (type 1, ...)           World Children
    ...
GRUP (type 0, label='DIAL')   Special hierarchy (see §2)
  DIAL record
  GRUP (type 7, ...)           Topic Children
    ...
```

Top-level groups (type 0) are sorted by signature. Each contains records of that type. CELL, WRLD, and DIAL have nested sub-group hierarchies.

---

## 8. Quick Reference: Sizes

| Structure             | Size (bytes) | Notes                    |
|-----------------------|-------------|--------------------------|
| Record header (SSE)   | 24          | All main records         |
| Record header (TES4)  | 20          | Oblivion records         |
| Group header          | 24          | All GRUP headers         |
| Subrecord header      | 6           | TES4/TES5 (2-byte size) |
| Subrecord header TES3 | 8           | Morrowind (4-byte size)  |
| HEDR subrecord data   | 12          | float + uint32 + uint32  |
| OBND subrecord data   | 12          | 6 × int16               |
| FormID                | 4           | uint32                   |
| Signature             | 4           | char[4]                  |

---

## 9. wbNewHeaderAddon (Legacy)

**Source**: `wbInterface.pas` lines 160–161

```pascal
wbForceNewHeader  : Boolean  = False;
wbNewHeaderAddon  : Cardinal = 40;    // value 40 = form version for new records
```

This is **not** extra bytes added to the header. It's used during save to write additional form version info when `wbForceNewHeader` is True (for converting old format files). The value 40 is a form version number, not a byte count. The actual header struct remains 24 bytes.
