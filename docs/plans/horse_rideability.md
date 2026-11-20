# Rideable Horse Conversion: Oblivion CREA → Skyrim Mountable Actor

**Status: PLAN, unimplemented; feasibility measured 2026-11-19.** What a mountable
actor IS lives in [reference/skyrim_mountable_actor.md](../reference/skyrim_mountable_actor.md).

The converted horse already walks, fights and dies through the creature pipeline
([asset_convert_creature.md](../commentary/asset_convert_creature.md)); this is only the
delta for the ridden state.

## 1. The approach: transplant Skyrim's human half onto the Oblivion horse

Skyrim riding is two graphs in lockstep: the horse's own graph and the rider graph
`actors/character/behaviors/horsebehavior.hkx`, which runs on the rider's skeleton.
**The rider side is vanilla, shared by every horse, and must stay untouched.** It plays
`..\SharedKillMoves\Human&Horse\Paired_Mount.hkx` / `Paired_Dismount.hkx` from a fixed
path, so the human half of mount/dismount is always Skyrim's. Everything converted lives
on the horse side:

| Piece | Source | Work |
|---|---|---|
| Rider graph, 40 `horse_rider/*` clips | vanilla, already loaded by every character | none |
| Horse-side paired clips | **new file per clip:** the vanilla human tracks copied verbatim + the Oblivion horse half from `creatures/horse/idleanims/horsemount.kf` / `horsedismount.kf` as the `2_`-prefixed tracks | resample 1.400 s → 1.667 s (mount), 1.867 s → 2.133 s (dismount) so both actors share one clock |
| `SaddleBone` node | engine looks it up by name (global node-name table, beside `PerchFireNode`) | add to skeleton.nif + skeleton.hkx under `Bip02 Spine2`, the parent of Oblivion's own rider node `ActorParent` |
| Horse graph | `hkx_behavior.py` quadruped template | wrap the root in vanilla's `MountBehavior` shape: `pa_HorseEnter`/`pa_HorseExit` (+`Swim` variants) → `BSSynchronizedClipGenerator`, `PairEnd` back to the root state |
| Records | constants / authored data | RACE Mount Data; `ActorTypeHorse` (00026110) on the NPC_ (all 32 vanilla horses carry it) |

## 2. Measured facts behind it

- Oblivion authored BOTH halves of mounting: `horsemount.kf` (1.4 s) and
  `horsedismount.kf` (1.867 s) each animate 128 nodes: the horse's `Bip02` rig plus a full
  `Bip01` human rider parented under `ActorParent`. The horse half ships nowhere today
  (the converted `animations/` folder has no mount clip).
- The rider starts 69 units to the side (`Bip01 NonAccum` X −69 at t=0); Skyrim's
  default Mount Offset X is −63.48.
- Rig sizes match: head 139.6 vs 138.4, front hoof y 43.7 vs 43.2. The Oblivion back
  is lower: mesh top at the saddle (y≈0) 114.7 vs 121.1. The Oblivion rider ends seated
  with `Bip01 NonAccum` at ActorParent + 71.6 → seat ≈126.6 model z, vs vanilla
  `SaddleBone` 134.9. Place `SaddleBone` from that authored end pose, not from vanilla.
- The vanilla horse graph has **no ridden branch**: ridden and riderless use the same
  locomotion states. The rider graph mirrors it state for state (same event names,
  same clip names, blends on `HorseSpeedSampled` / `TurnDeltaDamped`).
- Vanilla horse and rider loops are cut to the same length (walk 1.100 s both).
  Oblivion's walk is 0.967 s, so the rider's sway drifts out of phase with the stride.
  The rider stays seated (`SaddleBone` rides on `Bip02 Spine2`); the drift is cosmetic.
- Oblivion horses DO have jump clips (`jumpstart/jumploop/jumpland.kf`).

## 3. Open risks (settle on the test instance)

1. **Who feeds the rider graph.** `HorseSpeedSampled` is an engine string; unknown whether
   the engine copies it from the horse's graph or computes it. If it reads the horse
   graph, ours must expose that variable (we already emit `BSSpeedSamplerModifier`).
2. **Event names.** The rider reacts to `moveStart`/`moveStop`/`turnLeft`/`turnRight`/
   `SprintStart`/`SprintStop`/`idleHeadshake`... If those reach the rider only when the
   horse graph raises them, our graph must raise the same names; otherwise the rider
   holds the idle pose while the horse moves.
3. **Seat height mismatch during the paired clip.** The vanilla human half is rooted at
   `PairedRoot → SaddleBone` of the vanilla horse; whether the engine aligns it to our
   `SaddleBone` or to the horse root decides whether the rider climbs to the right
   height or snaps ~8 units at `PairEnd`.
4. `HorseSpine2` is also an engine string (no code xref found yet); check what reads it.

## 4. Steps

1. `SaddleBone` in skeleton.nif/.hkx for the creature whose CREA animations include an
   authored mount clip (`idleanims/horsemount.kf`). That clip is the authored indicator of
   mountability, not the folder name.
2. Convert the horse half of the mount/dismount KFs and splice them with the vanilla human
   tracks into horse-project paired clips (vanilla clips come through
   `asset_convert/sources/skyrim_assets.py`, never `references/`).
3. `MountBehavior` wrapper in the horse's generated graph.
4. RACE Mount Data + `ActorTypeHorse` keyword.
5. Headless test: `placeatme` a converted horse, activate, record mount / ride / dismount.
