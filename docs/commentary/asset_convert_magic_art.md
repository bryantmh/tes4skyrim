# asset_convert/nif/magic_art.py - spell effect meshes

**Code:** `asset_convert/nif/magic_art.py`, `asset_convert/asset_pipeline.py` (`_split_magic_art`), `tes5_import/record_types/magic_art.py`

The records that point at these meshes (ARTO, PROJ, EXPL) are covered in
[tes5_import_magic.md](tes5_import_magic.md#magic-art).

## <a id="phase-meshes"></a>One Oblivion mesh becomes one mesh per job

An Oblivion MGEF names a single model (`Model.MODL`, e.g.
`MagicEffects\Fireball.nif`). That one NIF holds every stage of the spell as a
named `NiControllerSequence`:

| Oblivion sequence | Skyrim job | Written as | Driven by |
|---|---|---|---|
| `SpecialIdle_Cast` | casting art (ARTO, type 0) | `<stem>_cast.nif` | `Magic\CastingBasic.hkx`, sequence renamed `mCast` |
| `SpecialIdle_Projectile` | the bolt (PROJ) | `<stem>_projectile.nif` | `Magic\IdleOnLoad.hkx`, renamed `mIdle` |
| `SpecialIdle_AreaEffect` | the area burst (EXPL) | `<stem>_area.nif` | no graph: the engine plays `SpecialIdle_AreaEffect` by name |
| `SpecialIdle_HitEffect` | hit art (ARTO, type 1) | `<stem>_hit.nif` | `Magic\IdleOnLoad.hkx`, renamed `mIdle` |
| `SpecialIdle_SummonEffect` | hit art when there is no HitEffect | `<stem>_summon.nif` | `Magic\IdleOnLoad.hkx`, renamed `mIdle` |

Skyrim wants one mesh per job. Each is driven by a stock graph, or for an
explosion by the sequence name, so each derived mesh keeps only its own
sequence under the name its driver expects. The graphs and names are the
vanilla ones:

- Hand art uses `CastingBasic` (`mIdle`, `mReady` and `mIdleStaff` loop;
  `mIntro`, `mCharge` and `mCast` play once).
- Hit art uses `IdleOnLoad` with `mIdle` CLAMP.
- Projectiles use `IdleOnLoad` with `mIdle` LOOP.
- Explosions carry no graph and a single `SpecialIdle_AreaEffect` CLAMP
  (39 of 49 vanilla explosions).

**Every Oblivion phase sequence drives every emitter in the file.** The Cast
sequence switches the projectile, area and hit emitters off, and so on for
the others. That is why keeping one sequence is enough to silence the other
phases: nothing is deleted from the scene graph.

Phases are read from the **source** NIF, the same file the import stage reads,
so the records and the meshes always agree on which phases exist. The scan
looks for length-prefixed `SpecialIdle_<phase>` strings. Oblivion stores names
inline and Skyrim in a header string table, but both prefix each name with its
length, so one test reads either. A bare match with no length prefix is not a
sequence name.

Measured on Oblivion.esm (`--meshes-only --mesh-subdirs magiceffects`): 22
effect models converted, **65 phase meshes written from 20 models**, and 0 models
with no converted mesh.

## <a id="hand-loops"></a>The hand loops Oblivion never had

Oblivion shows nothing in the hand until the spell is released. Skyrim's
casting graph plays `mIntro`, `mIdle`, `mCharge`, `mReady` and `mIdleStaff` in
the hand before `mCast`, and a missing sequence leaves the hand empty for that
state.

Each loop is a clone of the Cast sequence. Its `NiPSysEmitterCtlr` birth rates
are replaced by a constant, and its emitter-active keys by a constant **on**
wherever the Cast phase ever turns that emitter on. The constant is the Cast
phase's peak rate times a share. The shares are the medians, over 11 vanilla
hand meshes, of each loop's emission relative to `mReady`:

| Loop | Cycle | Share of peak rate |
|---|---|---|
| `mIntro` | CLAMP | 0.23 |
| `mIdle` | LOOP | 0.5 |
| `mCharge` | CLAMP | 0.8 |
| `mReady` | LOOP | 1.0 |
| `mIdleStaff` | LOOP | 0.23 |

Controllers other than emitters (visibility, modifier-active, gravity, shader
floats and transforms) keep the Cast phase's own keys.

## <a id="menu-art"></a>Menu art is vanilla, not derived (reverted attempt)

The magic menu's display objects are vanilla Skyrim STATs, picked per effect
([tes5_import_magic.md](tes5_import_magic.md#menu-display-object)). Two rounds
of menu art derived from our own Cast phase failed in game.

**How vanilla menu art is built.** `FireballInvArt.nif` and `MagicHatMarker`
have:

- no `NiControllerManager`, no sequences and no graph;
- `BSX = 1`;
- free-running controllers flagged 0x48 (Active + Compute Scaled Time, cycle
  Loop);
- **visible triangle geometry beside the particles.** All 31 vanilla menu
  meshes have it (`MAGINV*.nif` plus `FireballInvArt`): glow cards, spheres and
  rings of radius 8 to 207.

**Round 1: invisible.** We built the Cast phase in that free-running form and
scaled the root by 3.39, the vanilla menu-to-hand ratio of median particle
reach. The spells showed nothing in the menu. 15 of our 20 menu meshes had no
visible geometry: every Cast-phase shape in them is hidden. The menu fits each
object to its geometry, and a particle system has no bounds until it has
emitted.

**Round 2: flicker.** We added an invisible octahedron sized to the particles'
reach. The art appeared, but it flickered:

- Flare blinked in and out.
- Drain Marksman jumped between positions on screen.

The Cast phase's own keys loop in the menu every 1.7 s: the node transforms
that launch the bolt, and one-frame flashes of the area rings. In the hand,
`mCast` plays once and the ready loops hold. A menu version would need every
non-emitter controller frozen to a steady pose, with nothing in Oblivion to say
which pose. Vanilla's MAGINV art is authored for the menu, so it replaced the
derived meshes.
