ScriptName TESGameSelectQuest extends Quest Conditional
{Threads of Prophecy — new-game game selector.

Driven from the MQ101 stage-0 takeover (see TESGameSelectMQ101.psc), which
parks the player in a holding cell, calls RunSelection() to ask the question,
and then either replays the vanilla opening or calls BeginChosenGame() to hand
off to a converted game.

Every foreign form is resolved at runtime with Game.GetFormFromFile(), so this
plugin masters only Skyrim.esm and ships to users with any subset of the
converted games installed, in any load order. A game whose plugin is absent
never appears in the menu.}

; ---------------------------------------------------------------------------
; Plugin file names. Properties rather than literals so a repack for a renamed
; or translated plugin needs no recompile — just an xEdit property edit.
; ---------------------------------------------------------------------------
String Property OblivionPlugin     = "Oblivion.esm"     Auto
String Property NehrimPlugin       = "Nehrim.esm"       Auto
String Property MorroblivionPlugin = "Morrowind_ob.esm" Auto
String Property FalloutNVPlugin     = "FalloutNV.esm"   Auto
String Property MorrowindPlugin     = "Morrowind.esm"   Auto
String Property ArktwendPlugin      = "Arktwend_English.esm" Auto

; ---------------------------------------------------------------------------
; Per-game entry points. GetFormFromFile takes a form's ID *within its own
; file*, so only the low 24 bits matter and the load order can be anything.
;
;   Oblivion      Charactergen 0002466E — stage 5 is the whole start: sets
;                 in-chargen, starts MQ01 at stage 5, and moves the player to
;                 CGPlayerStartMarker 00032AB5 in the Imperial Prison.
;   Nehrim        Charactergen 0002466E — stage 5 moves the player to
;                 PlayerMarkerStartCell 00000D33. MQ00 00000811 is the real
;                 intro driver and stops Charactergen once controls return.
;   Morroblivion  fbmwChargen 00F0A28C — stage 1 opens the prison-ship
;                 sequence; mwCGPlayerStartMarker 00F0A278 is the wake-up spot
;                 (the stage-1 fragment does NOT move the player itself).
; ---------------------------------------------------------------------------
Int Property OblivionChargenID     = 0x0002466E Auto
Int Property OblivionStartMarkerID = 0x00032AB5 Auto
Int Property OblivionChargenStage  = 5          Auto

Int Property NehrimChargenID       = 0x0002466E Auto
Int Property NehrimStartMarkerID   = 0x00000D33 Auto
Int Property NehrimMainQuestID     = 0x00000811 Auto
Int Property NehrimChargenStage    = 5          Auto

Int Property MorroChargenID        = 0x00F0A28C Auto
Int Property MorroStartMarkerID    = 0x00F0A278 Auto
Int Property MorroChargenStage     = 1          Auto

; FalloutNV  VCG00 00102037 — stage 0 IS the whole opening: it moves the player
;            to VCG01PlayerStartMarkerREF 00103E6B, sets the hour, forces the
;            cemetery weather, plays the intro and advances itself to stage 90,
;            which reaches Doc Mitchell's house. Nothing to dress the player in:
;            FalloutNV's player record carries only the Pip-Boy and its glove,
;            and that same stage opens by removing both.
Int Property FalloutNVChargenID     = 0x00102037 Auto
Int Property FalloutNVStartMarkerID = 0x00103E6B Auto
Int Property FalloutNVChargenStage  = 0          Auto

; Vanilla Morrowind has NO chargen quest and NO start marker to move to: the
; opening is object scripts, set running by the TES3 global CharGenState. The
; `Main` start script polls `CharGenState == 1` and launches `CharGen`, which
; positions the player itself (`PositionCell ... "Imperial Prison Ship"`).
; See: docs/commentary/morrowind_runtime.md#vanilla-morrowind-chargen
;
; 🛑 The probe must be a BASE record, like every other game's chargen quest —
; MWJ_A1_1_FindSpymaster, the main quest's opening journal. GetFormFromFile
; resolves a non-persistent REFR only while its cell is loaded, and the
; selector runs from Skyrim's holding cell, so a placed reference (the first
; version of this probe used CharGen_Bed) answers None however the load order
; looks and the game is never offered.
Int Property MorrowindProbeID       = 0x00192940 Auto

; CharGenState, the GLOB `Main` polls to launch `CharGen`. Setting it to 1 IS
; the start of vanilla Morrowind, which is what the TES3 engine did on a new
; game and what Skyrim's never does.
Int Property MorrowindChargenStateID = 0x006472DD Auto

; Arktwend is a TES3 total conversion and starts the same way: its own `Main`
; polls its own CharGenState, and its `CharGen` moves the player to
; "Melee, Monastery". The GLOB is a base record, so it is the probe as well.
Int Property ArktwendChargenStateID = 0x006472DD Auto

; ---------------------------------------------------------------------------
; Starting equipment. On a new game the player carries Skyrim's base-record
; inventory (16 debug items) — vanilla clears it with RemoveAllItems at MQ101
; stage 10, which the takeover prevents, so the handoff clears it instead and
; dresses the player in what the chosen game's author gave them: the TES4
; player base record's own inventory.
;
;   Oblivion      WristIrons + the Sack Cloth shirt/pants/sandals, all worn
;                 (Oblivion.esm NPC_ 00000007).
;   Nehrim        Flickweste / Geschnürte Lederhose / Jägermokassins worn,
;                 plus torch, Tagebuch and the anonymous MQ00 note carried
;                 (Nehrim.esm NPC_ 00000007).
;   Morroblivion  Morrowind_ob.esm does not override the player record, so a
;                 TES4 Morroblivion prisoner inherited Oblivion's set — its
;                 master file, so it is always present. Resolved from
;                 OblivionPlugin below.
; ---------------------------------------------------------------------------
Int Property OblivionWristIronsID  = 0x000BE335 Auto
Int Property OblivionShirtID       = 0x00027319 Auto
Int Property OblivionPantsID       = 0x00027318 Auto
Int Property OblivionShoesID       = 0x0002731A Auto

Int Property NehrimShirtID         = 0x0002ECAD Auto
Int Property NehrimPantsID         = 0x000229AB Auto
Int Property NehrimShoesID         = 0x0001C82B Auto
Int Property NehrimTorchID         = 0x00000D49 Auto
Int Property NehrimDiaryID         = 0x00000B96 Auto
Int Property NehrimNoteID          = 0x00000AED Auto

; ---------------------------------------------------------------------------
; The menu.
;
; Message.Show() renders a MESG record whose buttons are fixed at authoring
; time, but the set of installed games is only known at runtime. The vanilla
; mechanism for that is a CONDITION on each button (dunMiddenNamesMenuMSG does
; exactly this): a button whose condition fails is simply not drawn.
;
; Crucially, hidden buttons do NOT renumber the rest — Show() returns the
; button's ORIGINAL index, not its position among the visible ones. Vanilla's
; dunMiddenHandSculptureSCRIPT relies on this, testing `i == 0`..`i == 4`
; against fixed ring identities while its conditions hide arbitrary subsets.
; So the returned index maps directly onto the GAME_* constants below.
; ---------------------------------------------------------------------------
; The prompt, one variant per subset of the gated games. A MESG's DESC prologue
; names each game in turn and carries no condition, so the variant whose text
; names exactly the installed set is entry [installedMask] of this list. Bit 0
; is Oblivion, bit 1 Morroblivion, bit 2 Nehrim, bit 3 FalloutNV, bit 4
; Morrowind, bit 5 Arktwend — the gated BUTTONS order in
; make_game_select_esp.py.
FormList Property Menus Auto

GlobalVariable Property HasSkyrim       Auto
GlobalVariable Property HasOblivion     Auto
GlobalVariable Property HasNehrim       Auto
GlobalVariable Property HasMorroblivion Auto
GlobalVariable Property HasFalloutNV    Auto
GlobalVariable Property HasMorrowind    Auto
GlobalVariable Property HasArktwend     Auto

; Set true the moment the menu has been shown, so a second entry (quest
; restart, re-add on an existing save, a stray SetStage) can never re-ask.
; Persisted in the save as quest state, unlike a script-local.
Bool Property HasRun = false Auto Conditional

; The game the player picked, as a GAME_* id. Read by the MQ101 takeover to
; decide whether to release the vanilla opening or leave for another game.
Int Property ChosenGame = 0 Auto Conditional

; Game identifiers, in the button order the MESG declares.
Int Property GAME_SKYRIM       = 0 AutoReadOnly
Int Property GAME_OBLIVION     = 1 AutoReadOnly
Int Property GAME_MORROBLIVION = 2 AutoReadOnly
Int Property GAME_NEHRIM       = 3 AutoReadOnly
Int Property GAME_FALLOUTNV    = 4 AutoReadOnly
Int Property GAME_MORROWIND    = 5 AutoReadOnly
Int Property GAME_ARKTWEND     = 6 AutoReadOnly

; Number of games offered, counting Skyrim. 1 means "Skyrim only" — no menu.
Int gameCount

; Bitmask of the installed gated games, picking the MESG variant whose prologue
; names exactly them: bit 0 Oblivion, 1 Morroblivion, 2 Nehrim, 3 FalloutNV,
; 4 Morrowind, 5 Arktwend.
Int installedMask

; ---------------------------------------------------------------------------
; Selection: show the menu and record the choice. NO side effects — the
; takeover restores engine-default control/chargen state between this and
; BeginChosenGame(), so nothing done here would survive anyway.
;
; Called ONLY from the MQ101 stage-0 takeover, never from OnInit: OnInit on a
; Start-Game-Enabled quest can fire more than once (it runs again when the
; quest is restarted or re-added to a save), which is what made the menu pop
; up twice in the very first build. The retargeted stage-0 fragment runs
; exactly once per new game.
; ---------------------------------------------------------------------------
Function RunSelection()
  If HasRun
    Return
  EndIf
  HasRun = true

  DetectInstalledGames()

  ; Only Skyrim present — nothing worth asking. The takeover replays the
  ; vanilla opening.
  If gameCount <= 1
    ChosenGame = GAME_SKYRIM
    Return
  EndIf

  ; The returned index is the button's own index in the MESG, unaffected by
  ; which buttons the conditions hid — so it IS the game id.
  Int game = (Menus.GetAt(installedMask) as Message).Show()

  ; An unexpected index (a mod-added button, a cancelled menu) is treated as
  ; Skyrim: the safe direction, since it leaves the vanilla start intact.
  If game < GAME_OBLIVION || game > GAME_ARKTWEND
    game = GAME_SKYRIM
  EndIf
  ChosenGame = game
EndFunction

Bool Function ChoseSkyrim()
  Return ChosenGame == GAME_SKYRIM
EndFunction

; ---------------------------------------------------------------------------
; Handoff: dress the player and start the chosen game. Called by the takeover
; after it has restored controls and cleared the chargen state; if the chosen
; game turns out broken, this resets ChosenGame to Skyrim and the takeover
; runs the vanilla opening instead.
; ---------------------------------------------------------------------------
Function BeginChosenGame()
  If ChosenGame == GAME_OBLIVION
    BeginOblivion()
  ElseIf ChosenGame == GAME_NEHRIM
    BeginNehrim()
  ElseIf ChosenGame == GAME_MORROBLIVION
    BeginMorroblivion()
  ElseIf ChosenGame == GAME_FALLOUTNV
    BeginFalloutNV()
  ElseIf ChosenGame == GAME_MORROWIND
    BeginTes3(MorrowindPlugin, MorrowindChargenStateID)
  ElseIf ChosenGame == GAME_ARKTWEND
    BeginTes3(ArktwendPlugin, ArktwendChargenStateID)
  EndIf

  ; FalloutNV and the TES3 games show their own menus — FalloutNV at VCG01
  ; stage 36 (Doc Mitchell's reflectron), Morrowind from CharGenRaceNPC, the
  ; dock guard, who calls EnableRaceMenu once he has asked where you are from,
  ; and Arktwend from its own `CharGen`. Asking here too would put one up
  ; before any of them had spoken.
  If ChoseSkyrim() || ChosenGame == GAME_FALLOUTNV \
     || ChosenGame == GAME_MORROWIND || ChosenGame == GAME_ARKTWEND
    Return
  EndIf

  ; The TES4 engine popped the race menu (with the name prompt) automatically
  ; on every new game — in the Imperial cell, in Nehrim's start cave, on the
  ; prison ship. Skyrim's engine only shows it when a script asks, so ask now
  ; that the chosen game's opening cell is up. The same beat vanilla's own
  ; quickstart uses: moveto, a settling Wait, then ShowRaceMenu (menus queue
  ; behind each other, so the converted intro simply resumes when it closes).
  ;
  ; FalloutNV is the EXCEPTION and returns above: its intro shows its OWN race
  ; menu, authored as Doc Mitchell's reflectron at VCG01 stage 36. Asking here
  ; too put one up before Doc had spoken, then his lines, then the real one.
  Utility.Wait(0.5)
  Game.ShowRaceMenu()
EndFunction

; ---------------------------------------------------------------------------
; Detection
; ---------------------------------------------------------------------------

Function DetectInstalledGames()
  gameCount = 0
  installedMask = 0

  ; Skyrim is always available — it is the game we are running inside, and it
  ; owns no mask bit because its prologue line is unconditional.
  SetGate(HasSkyrim, true, 0)
  SetGate(HasOblivion, IsPluginPresent(OblivionPlugin, OblivionChargenID), 1)
  SetGate(HasMorroblivion, \
          IsPluginPresent(MorroblivionPlugin, MorroChargenID), 2)
  SetGate(HasNehrim, IsPluginPresent(NehrimPlugin, NehrimChargenID), 4)
  SetGate(HasFalloutNV, \
          IsPluginPresent(FalloutNVPlugin, FalloutNVChargenID), 8)
  SetGate(HasMorrowind, \
          IsPluginPresent(MorrowindPlugin, MorrowindProbeID), 16)
  SetGate(HasArktwend, \
          IsPluginPresent(ArktwendPlugin, ArktwendChargenStateID), 32)

  ; One line naming what was found. A menu that never appeared, or appeared
  ; with the wrong buttons, is ALWAYS this pass: gameCount 1 means nothing was
  ; detected and the takeover runs the vanilla opening with no prompt at all.
  Debug.Trace("[TESGameSelect] detected " + gameCount + " game(s), mask " \
              + installedMask)
EndFunction

Function SetGate(GlobalVariable gate, Bool present, Int maskBit)
  ; The global drives that button's MESG condition: 1 shows it, 0 hides it.
  If gate != None
    If present
      gate.SetValue(1.0)
    Else
      gate.SetValue(0.0)
    EndIf
  EndIf

  If present
    gameCount += 1
    installedMask += maskBit
  EndIf
EndFunction

Bool Function IsPluginPresent(String plugin, Int probeID)
  ; GetFormFromFile returns None when the file is not in the load order, so a
  ; successful lookup of a form we know that file defines proves it is loaded.
  ;
  ; It takes the form's id WITHIN ITS OWN FILE — the low 24 bits — so a plugin
  ; that masters Skyrim.esm still probes as 0x00xxxxxx even though its records
  ; carry index 01 on disk.
  Bool found = Game.GetFormFromFile(probeID, plugin) != None
  If !found
    Debug.Trace("[TESGameSelect] " + plugin + " not found (probe " \
                + probeID + ")")
  EndIf
  Return found
EndFunction

; ---------------------------------------------------------------------------
; Per-game handoff
;
; Each converted game's chargen quest owns its own opening: its stage result
; script moves the player, starts the companion quests, and disables or
; enables controls to suit its own intro. We only dress the player, move them
; to that game's start marker, and set the stage its own author wrote as "the
; game begins here" — then get out of the way. Skyrim's opening never started:
; the takeover replaced the fragment that would have launched it.
; ---------------------------------------------------------------------------

Function BeginOblivion()
  Quest chargen = GetQuestFrom(OblivionChargenID, OblivionPlugin)
  If chargen == None
    FallBackToSkyrim()
    Return
  EndIf

  StripPlayer()
  WearFrom(OblivionPlugin, OblivionShirtID)
  WearFrom(OblivionPlugin, OblivionPantsID)
  WearFrom(OblivionPlugin, OblivionShoesID)
  WearFrom(OblivionPlugin, OblivionWristIronsID)

  HandOff(chargen, OblivionChargenStage, \
          GetRefFrom(OblivionStartMarkerID, OblivionPlugin))
EndFunction

Function BeginNehrim()
  Quest chargen = GetQuestFrom(NehrimChargenID, NehrimPlugin)
  If chargen == None
    FallBackToSkyrim()
    Return
  EndIf

  StripPlayer()
  WearFrom(NehrimPlugin, NehrimShirtID)
  WearFrom(NehrimPlugin, NehrimPantsID)
  WearFrom(NehrimPlugin, NehrimShoesID)
  CarryFrom(NehrimPlugin, NehrimTorchID)
  CarryFrom(NehrimPlugin, NehrimDiaryID)
  CarryFrom(NehrimPlugin, NehrimNoteID)

  HandOff(chargen, NehrimChargenStage, \
          GetRefFrom(NehrimStartMarkerID, NehrimPlugin))

  ; Nehrim's intro is driven by MQ00, not by Charactergen — Charactergen only
  ; places the player, and MQ00 stage 2 is what stops it and hands controls
  ; back. In Nehrim itself MQ00 is Start-Game-Enabled, but that flag only fires
  ; for Nehrim's OWN new game, so start it explicitly here.
  ;
  ; Its stage is deliberately NOT set: Nehrim's GlobalplayerScript polls
  ; `If StartQuest == 0 -> MQ00.SetStage(1)` and drives the opening from there,
  ; so forcing a stage would race that script rather than help it.
  Quest mq00 = GetQuestFrom(NehrimMainQuestID, NehrimPlugin)
  If mq00 != None && !mq00.IsRunning()
    mq00.Start()
  EndIf
EndFunction

Function BeginMorroblivion()
  Quest chargen = GetQuestFrom(MorroChargenID, MorroblivionPlugin)
  If chargen == None
    FallBackToSkyrim()
    Return
  EndIf

  ; A TES4 Morroblivion prisoner wore Oblivion's starting set — Morroblivion
  ; never overrides the player record, so it inherited its master file's.
  StripPlayer()
  WearFrom(OblivionPlugin, OblivionShirtID)
  WearFrom(OblivionPlugin, OblivionPantsID)
  WearFrom(OblivionPlugin, OblivionShoesID)
  WearFrom(OblivionPlugin, OblivionWristIronsID)

  HandOff(chargen, MorroChargenStage, \
          GetRefFrom(MorroStartMarkerID, MorroblivionPlugin))
EndFunction

Function BeginFalloutNV()
  Quest chargen = GetQuestFrom(FalloutNVChargenID, FalloutNVPlugin)
  If chargen == None
    FallBackToSkyrim()
    Return
  EndIf

  ; Nothing to wear: VCG00 stage 0 strips the Pip-Boy the player record carries,
  ; so only Skyrim's own debug inventory has to go.
  StripPlayer()

  HandOff(chargen, FalloutNVChargenStage, \
          GetRefFrom(FalloutNVStartMarkerID, FalloutNVPlugin))
EndFunction

; A TES3 game (vanilla Morrowind, Arktwend) has no chargen quest: its opening
; is object scripts gated on the TES3 global CharGenState, and setting that to
; 1 is the whole start. `Main` — which the runtime starts by itself — then
; launches `CharGen`, which positions the player itself (the Imperial Prison
; Ship; Arktwend's monastery), so no marker is moved to and no stage is set.
;
; The global is a real GLOB in the converted plugin, which is why this is a
; plain SetValue: MorrowindRuntime mirrors it back into its own global space
; each tick, so a write here reaches the scripts polling it.
;
; Morrowind's player record carries no inventory (the gear comes from the
; census office stuff room), so stripping Skyrim's debug items is the rest.
; See: docs/commentary/morrowind_runtime.md#vanilla-morrowind-chargen
Function BeginTes3(String plugin, Int chargenStateID)
  GlobalVariable chargen = \
      Game.GetFormFromFile(chargenStateID, plugin) as GlobalVariable
  If chargen == None
    FallBackToSkyrim()
    Return
  EndIf

  StripPlayer()
  chargen.SetValue(1.0)
  Debug.Trace("[TESGameSelect] " + plugin + " CharGenState " + chargen \
              + " set to 1")
EndFunction

Function FallBackToSkyrim()
  ; The plugin is installed but its chargen quest is missing or renamed.
  ; Nothing has been touched yet — the takeover reads ChosenGame and runs the
  ; vanilla opening.
  Debug.Trace("[TESGameSelect] chargen quest missing; resuming Skyrim")
  ChosenGame = GAME_SKYRIM
EndFunction

Quest Function GetQuestFrom(Int formID, String plugin)
  Return Game.GetFormFromFile(formID, plugin) as Quest
EndFunction

ObjectReference Function GetRefFrom(Int formID, String plugin)
  Return Game.GetFormFromFile(formID, plugin) as ObjectReference
EndFunction

; Clear Skyrim's base-record starting inventory — the same RemoveAllItems
; vanilla runs at MQ101 stage 10 (and in every debug quickstart) before
; dressing the player.
Function StripPlayer()
  Game.GetPlayer().RemoveAllItems()
EndFunction

Function WearFrom(String plugin, Int formID)
  Form item = Game.GetFormFromFile(formID, plugin)
  If item != None
    Game.GetPlayer().AddItem(item, 1, true)
    Game.GetPlayer().EquipItem(item, false, true)
  EndIf
EndFunction

Function CarryFrom(String plugin, Int formID)
  Form item = Game.GetFormFromFile(formID, plugin)
  If item != None
    Game.GetPlayer().AddItem(item, 1, true)
  EndIf
EndFunction

Function HandOff(Quest chargen, Int stage, ObjectReference marker)
  ; Move first so the destination cell loads while the quest spins up. Some
  ; chargen stage scripts move the player to the same marker themselves — a
  ; redundant MoveTo is harmless — but Morroblivion's does not, so the move
  ; here is what actually delivers the player.
  Actor player = Game.GetPlayer()
  If marker != None
    player.MoveTo(marker)
  EndIf

  ; Let the arrival cell finish loading before the game's opening (and the
  ; race menu BeginChosenGame shows next) run on top of it.
  Int guard = 0
  While !player.Is3DLoaded() && guard < 200
    Utility.Wait(0.1)
    guard += 1
  EndWhile

  If !chargen.IsRunning()
    chargen.Start()
  EndIf
  chargen.SetStage(stage)
EndFunction
