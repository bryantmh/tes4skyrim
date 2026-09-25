# TESRuntime: alchemy apparatus and the crafting bench

**Code:** `tes_runtime/tes/alchemy.cpp`, `tes_runtime/tes/alchemy_hooks.cpp`,
`tes_runtime/tes/crafting.cpp`, `tes_runtime/common/crafting.h`,
`tes_runtime/common/crafting_client.cpp`,
`tes5_import/record_types/apparatus.py`.

**Played in game while it lived in MorrowindRuntime; the move into TESRuntime
is not yet played.**

## <a id="alchemy-apparatus"></a>Alchemy apparatus open Skyrim's own menu

Using a mortar, alembic, calcinator or retort from the inventory does what
OpenMW's `ActionAlchemy` does -- refused in combat with `sInventoryMessage3`,
otherwise the alchemy window -- except the window is Skyrim's own crafting
menu, opened over the closed inventory. Brewing is Skyrim's: which effects
combine, skill gain, potion value and naming are untouched. The carried tools
only scale each effect's magnitude and duration. It serves Morrowind and
Oblivion plugins alike, with one formula, Morrowind's, which is why it lives
in TESRuntime and not in MorrowindRuntime: an Oblivion-only load order needs
no Morrowind runtime for it.

Skyrim has a live `APPA` record type -- 45 in Skyrim.esm, `Retort01Novice`..
`Retort05Master` with a `QUAL` grade -- but every one is hollow (no `MODL`, zero
`OBND` and `DATA`), and nothing in the engine reads `QUAL`. The import keeps
converting an apparatus to a MISC.

### The sidecar

Every plugin that owns an apparatus writes
`SKSE/Plugins/TESRuntime/<plugin>.apparatus.json`:

```json
{"version": 1, "plugin": "Morrowind.esm",
 "apparatus": [{"id": "apparatus_a_calcinator_01",
                "form": ["Morrowind.esm", 13208384], "type": 2, "quality": 0.5}],
 "settings": {"fPotionStrengthMult": 0.5, "fPotionT1MagMult": 1.5,
              "fPotionT1DurMult": 0.5, "sInventoryMessage3": "...",
              "sNotifyMessage45": "..."},
 "player": {"intelligence": 30, "luck": 30}}
```

`type` is TES3's `AppaType` order (mortar, alembic, calcinator, retort);
`quality` is on Morrowind's scale ([below](#apparatus-quality-scale)). Only a
TES3 source carries `settings` and `player`, taken from the merged master
chain `morrowind_sidecar_source.gather` already reads: the export holds no
GMSTs. Morrowind's three potion GMSTs equal OpenMW's defaults, which the
runtime uses without them; the two messages have no default, so without a TES3
sidecar the refusals are silent, as they were before the move. `player` is the
base `player` record (30 and 30 in Morrowind.esm), not the live character.

### Four vtable swaps, measured on 1.6.1170 and confirmed on 1.6.659

- **InventoryMenu slot 1, Accept (`0x92d2a0`).** It registers every movie
  callback through the processor's slot 1 as `(processor, name, callback)`.
  The swap hands Accept a proxy processor that substitutes our function for
  the `ItemSelect` callback (`0x92d970`) and passes the rest through. Selecting
  a MISC otherwise reaches Skyrim's equip toggle (`0x6ca610`), which does
  nothing for a form that cannot be equipped and raises no event.
  **The inventory leaves through its own `CloseTweenMenu` callback
  (`0x92d920`)** before its close is posted. TweenMenu stays OPEN, hidden,
  under an inventory it opened -- the movie's `ShowTweenMenu` re-shows it with
  message 0 -- and only that callback posts it kMessage_Close. Closing the
  inventory alone left TweenMenu up with nothing drawn once alchemy closed.
- **AlchemyMenu slot 5, ProcessUserEvent (`0x90d010`).** The event at
  `UserEvents+0x300` -- what the `CraftButtonPress` callback sends, and the
  craft key -- starts a brew (`0x909a30`). With no mortar carried it is
  refused with `sNotifyMessage45` and nothing is consumed, OpenMW's
  `Result_NoMortarAndPestle`.
- **CraftingMenu slot 4, ProcessMessage (`0x900200`)** -- the
  [crafting bench](#crafting-bench).
- **ModEffectivenessFunctor slot 1 (`0x906e90`).** Per effect it reads the
  magnitude and duration, puts the effect's MGEF at `player+0xb98` for the
  perk conditions, runs entry point 66 and the skill factor on each (bits 21
  and 22 gate them), rounds half up and writes back. The swap runs it, then
  multiplies by the tools' ratio through the engine's own `SetMagnitude` /
  `SetDuration`, which refuse on No Magnitude / No Duration and recompute the
  cost (`0x143660`, which reads the base cost at MGEF `+0x6c`).

Vanilla scales potency per effect already -- `Benefactor` and `Poisoner` are
entry point 66 perks conditioned on the effect's keyword (function 501) and
on poison-making (500) -- so the functor is the engine's own per-effect seam.

### The ratio

OpenMW's pre-tool value is `x / fPotionT1MagMult / baseCost` (duration:
`fPotionT1DurMult`), `x = (Alchemy + 0.1 Int + 0.1 Luck) * mortar quality *
fPotionStrengthMult`. `applyTools` is ported line for line: the retort for a
helpful effect, the alembic for a harmful one (Morrowind's Harmful arrives as
Hostile), combined with a calcinator, and a harmful effect DIVIDED unless the
calcinator stands alone. The multiplier is that value over the same value
with a quality-1.0 mortar and nothing else, so a Journeyman mortar alone
leaves Skyrim's number exactly as it was and the mortar's quality is its own
factor. The best of each type in the inventory is used, as `setAlchemist`
picks. Converted MGEFs carry Power Affects Magnitude / Duration, without which
this functor would never scale them at all
([tes5_import_magic.md](tes5_import_magic.md#power-affects)). Alchemy is the
player's live actor value 16; Intelligence and Luck come from the sidecar's
`player`, and read 0 on an Oblivion-only load order.

Because `applyTools` is OpenMW's code ported line for line, `alchemy.cpp` is
GPL-3.0-derived, and so is the `TESRuntime.dll` binary
([morrowind_runtime.md](morrowind_runtime.md#licensing)).

### <a id="apparatus-quality-scale"></a>The quality scale is Morroblivion's

Oblivion stores quality as 10..100 (UESP's "strength" is that over 100),
Morrowind as 0.15..2.0. Morroblivion ported every Morrowind apparatus, keeping
the ids (`apparatus_a_mortar_01` is `0apparatusUaUmortarU01`), and all 22 pair
on the same grades:

| Morrowind | 0.15 | 0.5 | 1.0 | 1.2 | 1.5 | 2.0 |
|---|---|---|---|---|---|---|
| Morroblivion | 10 | 25 | 50 | 75 | 100 | 150 |

Oblivion.esm's grades are exactly 10/25/50/75/100. A TES4-format plugin's
quality is mapped back through that table, straight-line between grades, so
Morroblivion mode recovers the original Morrowind values exactly. Which scale
a plugin uses is read off its binary's magic, not its dialogue.

## <a id="crafting-bench"></a>The crafting bench: one owner, exported

CraftingMenu's `ProcessMessage` (`0x900200`) on `kMessage_Open` takes the
player's occupied furniture, reads the base's bench type at `+0xe0` and
switches through a jump table at `0x9007e8`. Type 5 allocates `0x1a0` bytes
from the Scaleform allocator, constructs `AlchemyMenu` (`0x903120`) with
`(this+0x10 movie, furniture base)`, stores it at `+0x30`, calls
`0x96fe40(0xf6)`, registers it with the FxDelegate at `+0x28` (`0xfbe870`) and
calls its slot 2; types 3 and 4 (Enchanting) do the same with `0x220` bytes,
`EnchantConstructMenu` and help id `0xf3`. With no furniture it builds
NOTHING, so the swap runs the original, then does exactly those steps on the
vanilla bench (`CraftingAlchemyWorkbench` `000BAD0C`, `WBDT 05 10`;
`CraftingEnchantingWorkbench` `000BAD0D`). The sub-menu keeps the base at
`+0x20`; no reference is placed.

Two callers open a bench: the apparatus (alchemy), and MorrowindRuntime's
enchanting service (`conversation.cpp`). `SwapVtableSlot` refuses a slot that
no longer holds the vanilla function, so only one DLL can own the swap.
TESRuntime owns it and exports `TESRuntime_OpenBench(bench, opened, closed)`
and `TESRuntime_BenchReady()`; `common/crafting_client.cpp` finds them with
`GetModuleHandle("TESRuntime.dll")` / `GetProcAddress`, so the enchanting
service needs TESRuntime loaded, and does nothing without it.
