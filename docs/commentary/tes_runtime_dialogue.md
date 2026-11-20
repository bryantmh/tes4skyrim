# TESRuntime: leaving a converted conversation

**Code:** `tes_runtime/tes/exit_hold.cpp`, `tes_runtime/tes/movie.cpp` (Scaleform helpers shared with the journal),
`tes_runtime/tes/test_game.cpp` (`Bye`), `tes_runtime/common/test_runner.cpp` (`bye locked`);
`tes5_import/packages/greet_hold.py`, `script_convert/static_scripts/TES4Polyfill.psc` (`HoldExitUntilAnswered`),
`TES4_ForceGreetHold.psc`, and `Fragment_Hold` in `TES4_ForceGreetDone.psc` / `TES4_ScriptPackageDone.psc`.

## No exit while choices are offered
<a id="no-exit-while-choices-are-offered"></a>

**Symptom.** Oblivion's Charactergen, stage 17: the player's controls are off (stage 14 runs
`disableplayercontrols`) and the Emperor force-greets. Pressing Tab at his choice list closed the conversation
("See you."). Stage 17 was never left: stage 18 is set only near the end of the conversation (INFO 0004D84C).
The Emperor ran no package afterwards (AI procedure -1), so he never greeted again, and the player could not walk
over to talk. With controls turned back on by console, talking to him replayed the greeting with no topics,
because every question is offered once (`Charactergen.what / who / why == 0`, set by its own answer) and they
are reachable only as choices of each other's answers.

**What Oblivion does** (Oblivion.exe, Nehrim install):

- `DialogMenu::HandleClick` (0x59f010) closes the menu on button id 3 (Goodbye) or 10 (its Xbox twin) with no
  condition. The rule lives in the button's visibility.
- When a line has spoken its last response, 0x59eb90 calls 0x59e2b0(show), which sets Goodbye's visible trait
  (tile +0x30, trait 0xFA1) together with the persuasion and service buttons. `show` is false when the line's
  menu-topic item has byte +8 set.
- The menu-topic constructor (around 0x6b8e5e) sets +8 when the INFO's linked-topics block (+0x30) has a
  choice list (`topicsLinkedTo`, xOBSE `TESTopicInfo::LinkedTopics`).
- With an empty topic list, 0x59e680 makes Goodbye visible again (0x59e9a4).
- The menu XML (`menus/dialog/dialog_menu.xml`) never sets Goodbye's visibility, and the Xbox exit copies it.
  So in Oblivion, once a line with choices has been spoken, the player cannot leave until they answer.

**Skyrim.** Vanilla `DialogueMenu.swf` sends Tab (`handleInput`), Escape (the `Cancel` callback) and its Exit
button's click through one method, `onCancelPress`. In the greeting state it skips the text; otherwise
`StartHideMenu` fades the movie and calls the engine's `CloseMenu` (51511). No record flag stops it (CK wiki:
"there isn't any way to avoid tabbing out of dialogue").

**Fix (opt-in).** The hold applies only to a conversation the source forced on the player, and only when a
converted script asks for it. No conversation the player starts, and nothing in vanilla Skyrim, is touched.

- **Converter.** A package that forces a conversation gets an OnBegin fragment that calls
  `TES4Polyfill.HoldExitUntilAnswered(akActor)`. A package's fragments all live in one script, so the call sits
  in whichever script the package already uses:
  - a force greet (TES4 Find or Ambush aimed at the player, `converter.forces_greet`) with no other fragment:
    `TES4_ForceGreetHold.Fragment_0`;
  - an armed, script-forced force greet: `TES4_ScriptPackageDone.Fragment_Hold` (`hosts.armed_gate(hold=True)`);
  - a StartConversation pool slot: `TES4_ForceGreetDone.Fragment_Hold` (`force_greets.pool_package(hold=True)`).
    The forced-flee pool (`force_flee.py`) uses the same builder without the hold.
- **Papyrus.** `HoldExitUntilAnswered` sends the mod event `TES4HoldExit` from the speaker, the way converted
  scripts already hand TESRuntime other requests (`spin.cpp`).
- **Runtime.** TESRuntime keeps the armed speakers by reference handle index. It gives the Dialogue Menu's movie
  its own `onCancelPress` and keeps the movie's original under `TESRT_onCancelPress`. The exit is held while all
  of these are true:
  - the conversation's speaker (topic manager +0x68, low 20 bits) is armed;
  - the line MenuTopicManager builds the next list from has choices: the item at manager +0x18 (else +0x38),
    its byte +0x10 set and +0x48 clear, its INFO at +0x30. These offsets are read off the choice-list builder
    35287 (1.6.1170 0x5e1830, 1.6.659 0x594010);
  - the engine's own choice lookup for that INFO (25659, 0x3ed950) returns a list;
  - the topic list is not empty, which is Oblivion's empty-list rule.

  While the exit is held, the method does nothing and the Exit button is hidden. Otherwise it calls the
  original. When the armed speaker's conversation closes, the speaker is disarmed.
- **Idle timer.** Skyrim also ends a conversation by itself when the player sits idle: the speaker's countdown
  on its AIProcess (actor +0xF8; 39301 reads it, 39302 adds to it, both on process +0x10 -> +0x340, 1.6.1170
  0x6e0a00 / 0x6e0a20) is filled from `fAIInDialogueModewithPlayerTimer` when the conversation starts (0x66085e)
  and on each new topic (0x67a307). When it runs out, the speaker says goodbye ("Bye.") and the menu closes,
  without going through `onCancelPress`. While the exit is held, the runtime tops the countdown back up to what
  it held when the hold began, so the timer stands still. The game setting itself is not touched; MorrowindRuntime
  rescales it for test pacing.

The force greet's OnBegin runs when the package starts (the actor sets off toward the player), before its
conversation opens. A first version made the hold automatic for every converted line with choices (by the
plugin's header author). That was not what was asked: automatic runtime hooks hide conversion bugs, and the
user wants the converted scripts to opt in.

The hold starts as soon as the line with choices begins, not when it ends as in Oblivion. In Oblivion, Goodbye
pressed during such a line leaves the same dead end. One deliberate side effect: Tab no longer skips a greeting
line with choices; a mouse click still does.

**Measured** (instance 1, `chargen_emperor_in_cell` from its checkpoint):

- Before: `bye` closed the conversation at the greeting's choice, "See you.", stage 17 with `what = who = why = 0`.
- After: both `bye locked` presses refused (logged "the player cannot leave"), every question and "What should I
  do?" asked, Renault's "Please, sire, we must keep moving." (sets 18).

Five more passes of `chargen_emperor_in_cell` + `chargen_secret_wall` held both refusals and reached stages
18 and 19 every time.

Opt-in build (the hold armed by CGEmperorGreetPlayerInCell's OnBegin fragment): 5 of 5 runs held both refusals
and asked every question; run 3 went on to 18 and 19. The same DLL with the plugin from before the fragment
existed: `bye locked` failed in 5 of 5 ("Goodbye was not locked: the conversation was left"), so the test catches
the bug.

Idle timer (scratch case: `wait 2100` ticks, about 70 real s, at the greeting's choices, then ask): before, the
Emperor said "Bye." 59 s after the choices appeared and the ask found no conversation open; after, the
conversation was still open and "What's going on?" was answered. The real case now carries that wait.

**Silences.** The main-thread tick touches the movie only when the Dialogue Menu opens or the hold flips. Before a
conversation it only asks whether the menu is open. A first version posted a UI task every 33 ms whenever the
engine named a speaker, which included the Emperor's stage-16 line. Its silence before "Let me see your face..."
read 6.6, 6.9, 6.6, 7.8 and 5.7 s. With the hold not installed it read 3.0, 5.7 and 5.8 s. With the current tick
it read 5.8, 6.3, 1.8, 6.8 and 2.2 s, within this measure's documented noise
([quiet](headless_testing.md#quiet)). On the opt-in build it read 6.2, 6.6, 5.4, 5.4 and 1.0 s with the hold
fragment, and 4.9, 5.1, 6.3 and 5.9 s without it (the fifth run had no "seen you" line), so the case's
`expect quiet <= 6` limit sits inside this gap's noise with or without the hold.

Two attempts went wrong first:

- `SetMember` on the movie clip needs `isDisplayObject` set; without it the game crashed when the menu opened.
- A held Tab that called `SkipText` cut the "What's going on?" answer off after its first line, and the menu kept
  the old topic. A held Tab now does nothing.

**Test.** The runner's `bye locked` presses Tab the way a player does: TES `Bye` invokes the movie's cancel when
the exit is held. The step passes only when the game refuses (`test_runner_test`:
`ByeLockedNeedsARefusal`). `oblivion_main/0000_chargen_cell.tes4test` presses it at the Emperor's first choice
and again during the first answer.

**Not covered.** A conversation closed by something other than the player (a fight, distance) still leaves the
re-offer of [said-once-retires-at-the-chain-end](tes5_import_dialogue.md#said-once-retires-at-the-chain-end),
which restarts at the greeting. A force greet that keeps Skyrim's Once Per Day does not greet again after its
conversation closes early ([package_ai_contracts](../reference/package_ai_contracts.md#a-force-greet-must-be-able-to-retire)).
