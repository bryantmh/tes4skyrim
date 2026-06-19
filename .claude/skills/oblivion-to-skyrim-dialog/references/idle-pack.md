# IDLE & PACK Conversion (TES4 → TES5)

IDLE converts reasonably faithfully; PACK does not — Skyrim's AI package system
is a procedure tree with no 1:1 to Oblivion's type-based packages. See the
`oblivion-dialog-system` / `skyrim-dialog-system` skills for full layouts.

---

## IDLE — Idle Animation

These are the gesture/animation records referenced by INFO responses and the AI
system. The structure is close between games.

### Field map

| Oblivion IDLE | Skyrim IDLE | Transform |
|---------------|-------------|-----------|
| `EDID` | `EDID` | Copy. |
| Model `MODL` (.kf path) | `DNAM` (FileName) | Copy the animation file path. **Verify the animation exists** in Skyrim's behavior set — Oblivion `.kf` anim names rarely match Skyrim's. A path that points at a non-existent Skyrim animation simply won't play. |
| Model `MODB` (bound radius) | — | No Skyrim equivalent; drop. |
| — | `ENAM` (Animation Event) | Skyrim drives idles via a named **animation event** in its behavior graph. Oblivion has no such field, so this must be supplied from the target Skyrim animation. **Judgment / asset knowledge** — without a valid ENAM, the idle won't fire in Skyrim's behavior system. |
| `CTDA[]` | `CTDA[]` | Translate via `conditions.md`. |
| `ANAM` (Animation Group Section U8) | `DATA.Animation Group Section` (U8) | Carry the value (DATA is marked unused/cpIgnore in Skyrim but the field exists). |
| `DATA` (Parent FormID, Prev Sibling FormID) | `ANAM` (Related Idles: Parent, Previous Sibling) | Copy/remap the two FormIDs into Skyrim's ANAM array. |

### Fidelity note

The *record* converts cleanly, but idle **playback** depends on Skyrim's behavior
graph (the `.hkx` behavior files), which is entirely different from Oblivion's.
An idle is faithful only if the referenced animation and its `ENAM` event are
valid in Skyrim. For idles used purely as dialogue gestures (INFO `SNAM`/`LNAM`),
the safest faithful result is to map them to an **existing Skyrim gesture idle**
rather than ship an Oblivion `.kf` that Skyrim can't play. **Judgment** — the
source data names an Oblivion animation, not a Skyrim one.

---

## PACK — AI Package (high loss, no faithful 1:1)

AI packages determine where/when an NPC is and what they do — which is *why an
NPC is present and available to talk*. This is the least convertible record in
the set.

### The mismatch

- **Oblivion PACK** is **type-based**: a single Type enum (Find/Follow/Escort/
  Eat/Sleep/Wander/Travel/Accompany/Use item/Ambush/Flee/Cast) plus fixed structs
  for Location (PLDT), Schedule (PSDT), and Target (PTDT), gated by conditions.
- **Skyrim PACK** is a **procedure tree**: a package template + a tree of
  procedures with input data, package-specific data, scheduling, and idle
  markers. There is no "Type enum + 3 structs" — the behavior is composed.

There is **no data-only transformation** that turns an Oblivion type-based
package into the equivalent Skyrim procedure tree with matching behavior.

### Best-effort mapping (skeleton + nearest template)

| Oblivion PACK piece | Skyrim approach |
|---------------------|-----------------|
| `PKDT.Type` (Find/Eat/Sleep/Wander/Travel/…) | Choose the **nearest Skyrim package template** (Skyrim ships standard templates: Sandbox, Eat, Sleep, Travel, Patrol, etc.). Map by intent: Eat→Eat/Sandbox, Sleep→Sleep, Wander→Sandbox, Travel→Travel, Find→Travel/Find. |
| `PLDT` (location: ref/cell/radius) | Feed into the template's location input data. Remap the FormID; carry the radius. |
| `PSDT` (month/day/time/duration) | Feed into the template's scheduling. Time/duration carry; the day-of-week combos are a rough match. |
| `PTDT` (target: ref/object/type) | Feed into the template's target input data; remap FormIDs (Player = 0x14). |
| `PKDT.Flags` | Carry the bits with Skyrim equivalents (Offers services, Must reach location, Must complete, sneak/swim/falls, weapons/armor unequipped). Several have no Skyrim equivalent. |
| `CTDA[]` | Translate via `conditions.md`. |

### What you get vs. what's lost

- **Achievable:** an NPC that is roughly in the right place, on a roughly similar
  schedule, doing a roughly similar high-level activity, gated by the same
  conditions. Enough that the NPC is *present and available to talk* at similar
  times — which is the part that matters for dialogue fidelity.
- **Lost / needs hand-authoring:** exact procedure behavior, idle-marker usage,
  conversation packages, force-greet nuance, escort/follow path behavior. A
  faithful escort or a precise force-greet must be rebuilt as a Skyrim package
  tree by hand. **Flag every non-trivial package.**

### Fidelity note for dialogue specifically

For dialogue, the package's job is mostly "put the NPC where the player can reach
them at the right time" and "offer services." Those high-level outcomes are
reproducible with the nearest-template approach. The fine-grained AI behavior is
not, and trying to force a literal field copy produces a non-functional package —
prefer the nearest valid Skyrim template over a structurally-translated but
behaviorally-dead one.

---

## Fidelity summary

| Record | Faithful from data alone? | Notes |
|--------|---------------------------|-------|
| IDLE record fields | Mostly | Needs a valid Skyrim animation + ENAM event (asset/judgment). |
| IDLE playback | No | Depends on Skyrim behavior graph; map gestures to existing Skyrim idles. |
| PACK presence/schedule (high-level) | Roughly | Nearest-template; enough for "NPC available to talk." |
| PACK fine behavior (escort/force-greet/convo) | **No** | Procedure tree must be hand-authored. |
