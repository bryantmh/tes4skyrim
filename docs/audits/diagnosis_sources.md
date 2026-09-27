# Which sources actually solved bugs — 2026-09-26

**Corpus:** the Claude Code session transcripts from 2026-09-23 to 2026-09-26.
35 diagnostic sessions were read, and 46 solved problems were traced back to the
fact that cracked each one. About 15 sessions were skipped because they were
planning or feature work with nothing to diagnose. The FNV guns and Morrowind
armor sessions were only skimmed. Which fact counts as "decisive" is a judgment
made from reading each session, so treat the counts as approximate.

This audit is the evidence behind the source order in
[CLAUDE.md](../../CLAUDE.md#verifying-your-work).

## Tally

| Source | Cracked the cause | Misled or wasted time | Examples |
|---|---|---|---|
| Authored data (`export/`) read against our `output/` and the code between them | ~16 | rarely | GameDaysPassed is a short in TES4 and a float in Skyrim; an autosave in CharacterGen stage 6; shelf placements that live only in the master; Nehrim's `Nehrim\` mesh-path double prefix |
| The user's in-game observations | ~9 | — | "vertical plane in the distance" (Nehrim blur sphere); "goes see-through while I watch" (shared shader); "works after a reload" (`.seq` missing a quest); "the land is there" (child worldspace uses the parent's LAND) |
| Skyrim exe disassembly | ~9 | ~3 | StartCombat does not retarget; the health getter drops the level term for non-autocalc NPCs; No Magnitude forces magnitude 1; a vendor faction needs PLVD; GetInCell matches one exact cell; greeting TCLT replaces the topic list. Wrong: the shared-condition-cache theory (the DLL patch changed nothing); the ServeTime second-move theory |
| The user's logs (Papyrus, crash, runtime, field reports) | ~8 | 1 | Attacker/Victim faction pair; OBSE `Call` on None; SetStage resetting quest variables; the crime-file error flood; the OnTrigger autosave burst; a stale `TES4Polyfill.pex`. Misled: one log full of console-command side effects |
| A working case put beside the broken one | ~7 | — | Troll race caster flag (diffed against a troll that fights); the crumbling wall and boards disproving the flag-8 theory; vanilla's dog getup graph and IDLE; vanilla's Namira scene |
| Live game reads | ~5 | ~6 | Worked: Paralysis 100 on the Gatekeeper (minutes, with the bug already onscreen); 0 topic lines in a fresh game; the shared shader pointer. Wasted: stutter probing, bone-arrow regen timing, troll AI poking, teleporting the user mid-quest |
| Vanilla census | ~3 alone; ~9 as the value to write | — | Nearly always answered what to write, not why: PLVD on 145/145 vendor factions, ForceGreet `(subtype, HELO)`, all 7,426 scene lines use their own quest's topic |
| OpenMW source | 3 | — | NPC activation rules; the crime globals; what a merchant sells |
| Git history | 3 | 1 | Arena `Start()` hoist dropped in the AST rewrite; candle glow maps losing their hard link. Misled: the gate speech blamed on the previous day's commit |
| Oblivion.exe disassembly | 1 | — | Abilities draw hit shaders only for six effect codes (Agronak's fire) |
| Oblivion/Nehrim install and Data folders | 2 | — | Distant LOD lists; the cattail mesh that only the unofficial patch fixes |
| CK wiki / UESP | 1 | ~3 | Right: OnHit's `akSource` is the bow, not the arrow. Wrong: the IgnoreFriendlyHits story, the escort load-door story, the flag-8 havok theory |
| Project docs and memory | ~4 | ~9 | Wrong: a memory note claiming a SpecialIdle fix that didn't exist; "conditioned quest targets are rare"; "the chest link works"; "Oblivion has no getup clips"; "3285 borrowed topics"; the health audit tool's formula; "the stutter fix" |
| SKSE source, xEdit, nif.xml | 0 alone | — | Named classes and confirmed layouts, which sped up disassembly |

## Findings

- **The most productive step was comparing the authored data against ours.**
  What did the author write, and what did we write? That question solved more
  problems than any external reference.
- **The user's observations and logs together cracked about as many bugs as
  disassembly did, and cost the session nothing.** Several sessions found the
  cause only after the user pointed at something the session had overlooked.
- **Disassembly is the source for why the engine does something.** It also
  produced confident wrong theories, so it pays to do it after the cheap data
  checks and after reading reference code that has already mapped the engine.
- **A vanilla census confirms what to write; it seldom explains the cause.**
- **Project docs and memory were wrong about twice as often as they helped.**
  Read them for leads, and check a claim before repeating it.
- **The live game split cleanly.** It was quick and decisive when the user
  already had the bug onscreen and offered it. It failed every time it was used
  to explore, or when it asked the user to replay something. Added logging has
  the same cost: a full build-and-play cycle for the user.
