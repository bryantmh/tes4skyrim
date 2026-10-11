# skyb_retarget — a Skyblivion mesh on a converted Oblivion creature

A side tool (to be vendored off) that puts a Skyblivion creature mesh on the
converter's own output for the matching Oblivion creature: Oblivion's
skeleton, animations, behavior graph, ragdoll and records, the Skyblivion art.
Hard-coding is allowed here; everything creature-specific lives in
`dreugh_map.py` (the Land Dreugh) and `tes4_plugin.py` (its record).

## <a id="what-it-does"></a>What it does

```bash
python -m skyb_retarget.build --skyb SKYBSkinLandDreugh.nif \
    --creature "<Oblivion Data>/Meshes/Creatures/LandDreugh"
```

1. **Fit** the Oblivion skeleton onto the Skyblivion mesh (`fit.py`).
2. **Retarget** every `.kf` onto the fitted skeleton and drive the legs from
   Oblivion's foot paths (`anim.py`, `leg_ik.py`).
3. **Convert**: stage a one-creature TES4 plugin plus the fitted creature
   folder and run the normal pipeline on it — `--import-mod`, `--export-only`,
   `--creatures-only`, `--import-only`. Nothing in the converter is special-cased.
4. **Body**: write the Skyblivion mesh as the project's body NIF (`body.py`).
5. **Package** `<work>/SKYBLandDreugh.zip`: the pruned ESP, the creature
   project, the CreatureRuntime animation fragment and `CreatureRuntime.dll`.

`<work>/report.json` records the ground lift, every capsule stretch, the
ragdoll bodies dropped and the tracks replaced per clip.

## <a id="verification"></a>Verification (Land Dreugh, 2026-10-08)

- `ragdoll_validate` on the fitted Oblivion skeleton: the same 16 sub-unit
  pivot mismatches the ORIGINAL has (authored), nothing new; 0 violations on
  the converted skeleton. unweighted bodies (hands, wings) dropped.
- Body NIF skinned at rest reproduces the Skyblivion mesh to 0.0002 units
  (+6.59 lift); its 64 bone nodes equal the converted skeleton's exactly.
- `animcache_validate` OK; `animdata_index_check`: 31 clips, 0 problems.
- Feet (tip height range / mean slide per frame while planted), Oblivion vs
  retargeted, all within ~1 unit in height and lower slide in every clip
  checked (idle, walk, run, back, strafe, turn, attacks, recoil, stagger),
  e.g. walk 0.50-1.01 -> 0.28-0.57, run 1.51-3.64 -> 0.73-1.76.
- Walk speed stays Oblivion's (155 u/s from the Speed attribute): the
  converter's speed bake (`hkx_anim.speed_bake_factor`) plays the shorter
  strides faster.

## <a id="the-test-plugin"></a>The test plugin

`tes4_plugin.py` writes `SKYBLandDreugh.esp`, a masterless TES4 plugin with one
CREA carrying the vanilla Land Dreugh's values (`Oblivion.esm` 0001FF25 per
UESP: level 17, 320 health, 30 melee, Greater soul) and its nine NIFZ parts.
Being masterless it has no spells (Oblivion's shock-touch ability and resist
abilities), faction, combat style, loot or sounds — those live in
`Oblivion.esm`. The converted ESP masters only `Skyrim.esm`.

## <a id="the-bone-correspondence"></a>The bone correspondence

The Skyblivion rig is 28 flat bones; Oblivion's is the `Bip01` tree.

| Skyblivion | Oblivion |
|---|---|
| `[body]` | split by y: `Bip01 Pelvis` (rear) / `Bip01 Spine01` (front) |
| `Tail1`, `Tail2`, `Tail3` (torso, upper torso, head) | `Spine02`, `Spine03`, `Head` |
| `FangL[00]` (rear abdomen) | `Tail01` |
| `Arm?[02]`, `Arm?Claw` | `?UpperArm`, `?ForeArm` (the blade is one piece, no pincer) |
| `Leg_?[10..14]` (front) | `?Calf1`, `?Foot1`, `?Foot2`, `?Foot3` |
| `Leg_?[30..34]` (rear) | `?Calf2`, `?Foot4`, `?Foot5`, `?Foot6` |

Hands and fingers carry no Skyblivion weight.

## <a id="fitting-the-skeleton"></a>Fitting the skeleton

Joints come from the painted weights, not the Skyblivion nodes (its right
front leg nodes are off-symmetric while the mesh is symmetric): a joint is the
centroid of the vertices weighted to both bones it joins; a foot tip is the
centroid of the lowest vertices of the last leg bones. The mesh is lifted so
the mean foot tip sits on z = 0 (its toe spikes reach z = -8).

Landmarked bones move onto those joints; limb bones (`SWING`) also turn so
their segment points at the next joint — the rear legs now run backward as
on the model. Every other bone keeps its rotation relative to its parent.

## <a id="the-ragdoll"></a>The ragdoll

`ragdoll_fit.py`: each `bhkRigidBody` keeps its offset from its bone; a capsule
on a swung segment stretches by the segment's length ratio; every
constraint's parent-side frame is re-seated rigidly with the child body, so
each joint still closes at rest. Bodies on bones that carry no weight and
have no weighted descendant (the hands) are removed with the constraints
naming them.

## <a id="the-horns"></a>The horns

The two horn-like pincers rising over the head are skinned in the Skyblivion
rig only to `Tail2` (the upper torso) and stay rigid on `Spine03`. Rigging
them down Oblivion's wing chain was tried and rejected in game (it looked
wrong); the wing bones carry no weight, so their ragdoll bodies are dropped.

## <a id="arm-reference-pose"></a>Arms: joint positions, not rotations

Rotation retargeting failed both ways. Measured from Oblivion's rest pose
(arms straight out), its idle's ~100° forearm fold landed on claws that
already pointed forward: "arms bunched up at the chest", crossing in every
attack. Measured from Oblivion's idle, the claws sat in the Skyblivion bind
pose — a near-T modeling pose — and attacks swung them past each other
(claw gap -35). Placing only the claw tip (FABRIK) left the solver free to
fold the elbow against the side with the forearm pointing outward.

`reach_ik.py` places the elbow (`ForeArm`) and the claw tip (`Hand`): each
Oblivion joint's offset from the `UpperArm` pivot, in the `Spine03` frame,
scaled by that joint's rest distance ratio; the bones first copy Oblivion's
world rotation (twist), then `UpperArm` turns toward the elbow spot and
`ForeArm` toward the claw spot. `STANCE` sets where the joints sit at rest:
Oblivion's idle offset plus `(1 - blend) x` the difference to the Skyblivion
rest offset, with Oblivion's movement away from its idle added on top.
Each joint has its own blend. Measured at 0.65 (elbow) and 0.75 (claw), left
arm (out, forward, up): idle upper arm (.82, .00, -.57), about 35° down,
forearm (.15, .96, -.23) pointing forward, slightly out; Oblivion
(.51, -.11, -.85) and (-.16, .97, .16), Skyblivion bind (.98, .18, .10) and
(.73, .52, -.45). Claw gap / Oblivion hand gap: idle 77 / 58, side swipes
15.1 / -2.0 and -3.6 / 6.4, power attack 75 / 60. Lower a blend for a wider
stance, raise it toward Oblivion's tucked arms.

## <a id="the-package"></a>The package

`esp_prune.py` cuts the ESP to the creature's own records (NPC_, RACE, ARMO,
ARMA, BPTD, MOVT, IDLE, CSTY, and the voice type the race names: 46 of 210);
the converter's shared machinery (attribute/AI factions, combat-approach
packages and quest, humanoid voice types, globals, settings) is left out, and
the NPC_'s faction memberships with it. The zip carries that ESP, `meshes/`,
the CreatureRuntime animation fragment and `CreatureRuntime.dll` — no
TESRuntime/MorrowindRuntime sidecars, no SEQ file. `output/` is untouched.

## <a id="the-body-mesh"></a>The body mesh

`body.py` rewrites the Skyblivion NIF in the converter's body layout: a root
named after the file holding a bare copy of the converted skeleton's bone
tree, every shape skinned to those nodes (top 4 influences, bind from the
fitted rest pose, partitions regenerated as body part 32 through
`skin_retarget`). The art, shaders and texture paths
(`Textures\Actors\SKYBLandDreugh\*.dds`) are Skyblivion's; the textures are not
part of this tool.

## <a id="retargeting-the-animations"></a>Retargeting the animations

`anim.py` runs `asset_convert.havok.clip_retarget.retarget_clip` (bone-local
rotation deviation from rest) between the two skeletons with the root folded
into its children, as the engine plays the accumulation root. The result is
written back into a copy of each ORIGINAL `.kf`, so text keys, priorities and
visibility channels survive and the converter builds the clips unchanged.

## <a id="planting-the-feet"></a>Planting the feet

Rotation retargeting alone left feet floating or sunk (idle: -7 to +13) and
sliding, because the Skyblivion legs are shorter and point differently.
`leg_ik.py` drives each leg per frame instead:

- the Oblivion foot tip's displacement from rest, in the body bone's frame,
  scaled level by a per-clip stride factor, is added to the Skyblivion foot's
  rest spot;
- the leg starts from its authored rest shape and a FABRIK solve over
  hip -> knee -> lower joint -> tip puts the tip on that spot;
- the stride factor is the leg-length ratio (~0.87), lowered per clip until
  every target is reachable, and the travel is scaled by the same factor —
  the root motion on `Bip01` and the in-place lunges/bobs on `Bip01 NonAccum`
  — so a planted foot stays planted and walk/run speeds follow the shorter
  legs. The clip is retargeted with its `Bip01` track removed
  (`retarget_clip` expects root motion split off); keeping it put the walk's
  travel on `NonAccum` a second time.
