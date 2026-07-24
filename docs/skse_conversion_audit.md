# SKSE / OBSE Convertibility Audit — Grounded in the Original Nehrim Scripts

**Date:** 2026-07-24
**Question:** Nehrim relies on OBSE. For a *faithful* conversion of its scripts, which OBSE (and Skyrim-removed vanilla-Oblivion) functions are **actually used**, and for each, is a faithful Skyrim conversion possible with **vanilla Papyrus**, only with **SKSE**, or **not at all**?

**Method (grep-based, reading the ORIGINAL source):**
1. **What is actually used** — extracted every function token invoked in the original Oblivion script source: `SCPT.SCTX` + `INFO.ResultScript` in `export/Nehrim.esm` (2,415 script bodies). Tool: `tools/obse_convertibility_audit.py`.
2. **Is it OBSE-added?** — a token counts as OBSE only if it appears in the xOBSE command set (`DEFINE_COMMAND*` / `CommandInfo kCommandInfo_*` across `references/xOBSE-master/`; 1,567 names) or is an OBSE compiler keyword (`Let`/`eval`/`Call`/`Function`/`ForEach`/`SetFunctionValue`/`loop`). Everything else used in the scripts is vanilla Oblivion.
3. **Does SKSE provide it?** — grepped every Papyrus native SKSE registers (`NativeFunctionN(...)` across `references/skse64-master/skse64/Papyrus*.cpp`; 644 names). Name-matching is a starting point; the per-function verdict below maps OBSE *capability* → best Skyrim target (OBSE and SKSE often name the same capability differently).
4. **Does the converter already target it?** — grepped `script_convert/constants.py` + `converter.py`.

> **Scope caveat — this measures the source, not correctness.** The counts are what the original scripts *invoke*, and the verdict is whether a faithful target *exists*. Whether the converter's emitted Papyrus is behaviorally correct is a separate, untested question — no converted Nehrim script has been runtime-verified. A function with a vanilla/SKSE target can still be mapped wrong.

---

## Headline finding

**Only 37 distinct OBSE-added functions are used across all 2,415 Nehrim script bodies** — and OBSE's dominant use is *not* arrays or strings, it is **user-defined functions** (`Call` 771×/383 scripts, `eval` 174×, `Let` 156×, `SetFunctionValue`/`Function`/`loop`/`ForEach`). Those are language constructs the converter already reshapes into the `TES4Call` method mechanism, and **SKSE is irrelevant to them** — they are a compiler feature, not an API gap.

Of the 37, after mapping each to its capability:

| Convertibility of the OBSE function | # functions | Notes |
|---|---:|---|
| **Vanilla Skyrim Papyrus** (no SKSE needed) | ~20 | incl. the UDF keywords, math, `PushActorAway`, `IsCasting`, `IsInAir`, `HasSpell`, `GetParentCell` |
| **SKSE required** for a faithful conversion | ~9 | input (`IsKeyPressed2`/`GetControl`/`GetAltControl`), INI (`SetNumericINISetting`), `GetStringGameSetting`, Scaleform UI (`SetMenuFloatValue`/`GetMenuHasTrait`/`SetMenuStringValue`) |
| **Neither** (no faithful target anywhere) | ~4 | OBSE arrays/strings (`ar_*`/`sv_*`), `PurgeCellBuffers`, `MessageEX`/`MessageBoxEX` formatting nuances |

**The functions SKSE would genuinely unlock touch a tiny footprint** — see the per-function table. The array/string "marquee" SKSE feature is used in **4 scripts total**.

**Separately**, the largest *inert* problem in Nehrim is **not OBSE at all** — it is vanilla-Oblivion commands that Skyrim's engine removed (path-based music, flames, disposition, weather, package inspection). These were never OBSE functions, so requiring SKSE does nothing for them; see the second table.

---

## The 37 OBSE-added functions actually used (with grounded verdict)

Counts are occurrences / distinct SCPT scripts. "Verdict" is the faithful-conversion target after mapping capability → Skyrim.

| OBSE function | occ | scripts | Faithful conversion | Verdict |
|---|---:|---:|---|---|
| `Call` (UDF invoke) | 771 | 383 | `TES4Call` method (converter mechanism) | **VANILLA** — already handled, no SKSE |
| `eval` | 174 | 174 | pass-through wrapper, dropped | **VANILLA** — already handled |
| `Let` | 156 | 5 | `x = x op y` rewrite | **VANILLA** — already handled |
| `loop` | 17 | 5 | `While`/`EndWhile` | **VANILLA** — already handled |
| `SetFunctionValue` | 6 | 3 | `Return X` | **VANILLA** — already handled |
| `ForEach` | 2 | 1 | `While i < arr.Length` (only if the container converts) | **VANILLA** *iff* the OBSE array does — here it iterates an `ar_*`, so effectively BLOCKED |
| `PushActorAway` | 50 | 25 | vanilla `ObjectReference.PushActorAway` | **VANILLA** — already handled |
| `SetActorsAI` | 55 | 12 | *(vanilla-Oblivion AI toggle; no Skyrim equivalent)* | **NEITHER** — see note; this is not OBSE-exclusive behavior |
| `IsCasting` | 9 | 9 | vanilla `GetAnimationVariableBool("bIsCastingRight"/"Left")` | **VANILLA** — already handled |
| `sin` / `cos` | 10 / 10 | 5 / 5 | vanilla `Math.Sin` / `Math.Cos` | **VANILLA** |
| `PurgeCellBuffers` | 6 | 3 | engine-internal memory op | **NEITHER** — no-op is correct (safe to drop) |
| `sv_destruct` (+ `sv_*`) | 3 | 2 | `StringUtil.*` — but only if the whole string-var dataflow is restructured | **SKSE (partial)** — needs restructuring; used in 2 scripts |
| `GetGameLoaded` | 2 | 2 | vanilla `OnPlayerLoadGame` event pattern | **VANILLA (approx)** |
| `GetStringGameSetting` | 19 | 1 | SKSE `Utility.GetINIString` is for INI, not GMST strings; no vanilla string-GMST read | **SKSE (partial)** / else NEITHER |
| `IsKeyPressed2` | 6 | 1 | SKSE `Input.IsKeyPressed` | **SKSE** |
| `GetAltControl` | 4 | 1 | SKSE `Input.GetMappedKey`/`GetMappedControl` | **SKSE** |
| `GetControl` | 4 | 1 | SKSE `Input.GetMappedKey` | **SKSE** |
| `ar_null` (+ `ar_construct`, `ar_*`) | 4+2 | 1 | SKSE `Utility.Create*Array`/`Resize*Array` — needs full restructuring | **SKSE (partial)** — 1 script |
| `SetNumericINISetting` | 4 | 1 | SKSE has INI *readers* (`Utility.GetINI*`); no INI *writer* → also `Game.SetGameSettingFloat` for GMSTs | **SKSE (partial)** |
| `GetParentCell` | 3 | 1 | vanilla `ObjectReference.GetParentCell` | **VANILLA** — already handled |
| `EnableKey` / `DisableKey` | 2 / 2 | 1 / 1 | SKSE `Input.EnableKey` / `DisableKey` | **SKSE** |
| `CloseAllMenus` | 2 | 1 | vanilla `Game.ForceThirdPerson`-style? No direct; `Input.TapKey(Esc)` hack | **NEITHER (approx only)** |
| `IsPlayable2` | 2 | 1 | vanilla `Form.IsPlayable` (SKSE also) | **VANILLA** |
| `SetMenuFloatValue` / `SetMenuStringValue` | 2 / 1 | 1 / 1 | SKSE `UI.SetFloat`/`SetString` — but by Scaleform target path, different model | **SKSE (partial)** |
| `PrintToConsole` | 2 | 1 | vanilla `Debug.Trace` (log, not console) / `Debug.Notification` | **VANILLA (approx)** |
| `GetMenuHasTrait` | 1 | 1 | SKSE `UI.GetBool`/`GetFloat` (Scaleform), different model | **SKSE (partial)** |
| `MessageBoxEX` / `MessageEX` | 2 / 1 | 1 / 1 | vanilla `Debug.MessageBox` / `Message` record; `%`-format args need manual expansion | **VANILLA (partial)** |
| `HasSpell` | 1 | 1 | vanilla `Actor.HasSpell` | **VANILLA** — already handled |
| `GetCrosshairRef` | 1 | 1 | SKSE `Game.GetCurrentCrosshairRef` | **SKSE** |
| `GetFullGoldValue` | 1 | 1 | vanilla `Form.GetGoldValue` | **VANILLA** |
| `GetGameRestarted` | 1 | 1 | no equivalent | **NEITHER** |
| `IsInAir` | 1 | 1 | vanilla `GetAnimationVariableBool("bInAir")` | **VANILLA** |

**Net SKSE-only unlocks (faithful, would otherwise be impossible):** the input family (`IsKeyPressed2`, `GetControl`, `GetAltControl`, `EnableKey`, `DisableKey` — **1 script**), `GetCrosshairRef` (**1 script**), and — only if you invest in restructuring — arrays/strings (`ar_*`/`sv_*`, **~3 scripts**) and the Scaleform-UI + INI-writer functions (**~2 scripts**). **On the order of 5–7 scripts total.**

---

## The real inert bulk is Skyrim-removed VANILLA commands — SKSE does nothing for these

These appear heavily in the original scripts but are **vanilla Oblivion commands, not OBSE** (verified absent from the xOBSE command set), so "require SKSE" is the wrong lever entirely. Counts are raw occurrences / scripts in the original `SCPT.SCTX`.

| Vanilla-Oblivion command | occ | scripts | Why no faithful Skyrim conversion |
|---|---:|---:|---|
| **Path-based music** (`StreamMusic` 38 + Nehrim's `emc*` plugin) | ~170 | **~143** | Skyrim music is `MusicType`-form-based; no engine (vanilla or SKSE) plays a track by file path. Needs authored `MUSC` records + a path→MusicType map. |
| **Flame toggles** (`HasFlames` 58, `AddFlames` 46, `RemoveFlames` 31) | ~135 | 8 | Skyrim lights carry no scriptable flame state. No vanilla or SKSE native. |
| **AI inspection** (`GetCurrentAIProcedure` 18, `GetCurrentAIPackage` 9, `GetIsCurrentPackage`) | ~30 | ~30 | SKSE registers **zero** package natives. `Actor.GetCurrentPackage` is vanilla but shallow. |
| **Weather** (`ForceWeather` 16, `SetWeather` 8) | ~24 | ~21 | Blocked because `WTHR` is in `SKIP_TYPES` (dangling FormID crashes the sky system) — an *engine/skip* problem, not an API one. Vanilla `SetWeather` Papyrus exists; convert WTHR to unlock. SKSE irrelevant. |
| `GetPlayerHasLastRiddenHorse` | 12 | 12 | Engine tracks no last-ridden horse. (SKSE has only a *setter*.) |
| `ModDisposition` / `GetDisposition` | ~18 | ~15 | Disposition removed from Skyrim's engine. No native anywhere. |
| `PositionCell`, `ForceFlee`, `SetForceSneak`, `SetActorsAI` | ~65 | ~15 | No vanilla or SKSE natives; approximations only. |

These ~200+ scripts are inert for engine-semantic reasons. **No amount of SKSE changes them.**

---

## Recommendation

**Do NOT require SKSE for the script conversion.** Grounded in the actual scripts:

- OBSE's heavy usage is user-defined functions and expression syntax — **already converted, no SKSE**.
- The functions SKSE would *faithfully* unlock touch **~5–7 scripts** (keyboard input, crosshair ref, and — only with real restructuring effort — arrays/strings and Scaleform UI/INI). Not worth a hard dependency.
- The genuinely large inert population is **Skyrim-removed vanilla commands** (music-by-path 143 scripts, AI-package inspection 29, weather 20, flames 8). **SKSE addresses none of them** — they were never OBSE functions.

**Highest-leverage work to reduce inert Nehrim scripts (all non-SKSE):**
1. **Path-based music → MusicType** (~143 scripts) — author `MUSC` records and map `emc*`/`StreamMusic`. Biggest single win in the whole audit.
2. **Convert `WTHR`** (remove from `SKIP_TYPES`) — unlocks the whole weather family via vanilla `SetWeather`/`ForceWeather`.
3. If SKSE is ever adopted for another reason, fold in the ~5–7 input/UI/array scripts opportunistically — but they don't justify the dependency alone.

---

## Reproduce

```bash
# The grounded per-function audit (this document's numbers):
python tools/obse_convertibility_audit.py export/Nehrim.esm

# Raw usage of the Skyrim-removed vanilla commands (music/flames/weather/etc.):
python tools/nehrim_script_command_census.py export/Nehrim.esm --grep \
    streammusic emc hasflames addflames removeflames forceweather setweather \
    moddisposition getcurrentaipackage getcurrentaiprocedure positioncell
```

`tools/obse_convertibility_audit.py` builds the OBSE-name set from xOBSE source and the SKSE-native set from skse64 source at run time, so re-running picks up any reference-tree changes.
