ScriptName TES4Polyfill Hidden
{Utility functions for converted TES4 Oblivion scripts.
All functions are Global — no instance needed.
Provides equivalents for Oblivion functions with no direct Papyrus mapping.}

; ==========================================================================
; Random
; ==========================================================================

Int Function GetRandomPercent() Global
  Return Utility.RandomInt(0, 99)
EndFunction

; ==========================================================================
; Cell / Location
; ==========================================================================

Bool Function IsInCell(ObjectReference akRef, Cell akCell) Global
  Return akRef.GetParentCell() == akCell
EndFunction

Bool Function IsInSameCell(ObjectReference akRef1, ObjectReference akRef2) Global
  Return akRef1.GetParentCell() == akRef2.GetParentCell()
EndFunction

; True while a TES4 `begin GameMode` block on this placed reference would run.
;
; Is3DLoaded() alone is WRONG here: an initially-disabled reference (record flag
; 0x800) has no 3D, so an Is3DLoaded()-gated poll can never start — and the poll
; body is frequently the only thing that ever calls Enable() on that very
; reference.  That deadlock is unbreakable: the script that enables the ref only
; runs once the ref is enabled.  It stranded 200 placed refs in Nehrim, Celebro
; (the intro companion, MQ00CelebroScript `if GetStage MQ00 == 5 / enable`)
; among them, so the intro NPC never appeared at all.
;
; Oblivion's own rule is cell-scoped, not 3D-scoped: GameMode ran for every ref
; in an active cell, disabled ones included — which is exactly how the vanilla
; self-enable idiom works.  So test parent-cell attachment, which is true for a
; disabled ref and false for anything outside the active grid.  That preserves
; the anti-storm property the 3D gate was introduced for (references in detached
; cells still never tick); it only stops treating "invisible" as "not there".
; The GameMode poll gate, used by every converted OnUpdate.
;
; NOT a bare Is3DLoaded().  An item INSIDE A CONTAINER has no 3D and no
; parent cell, and calling Is3DLoaded() on it raises
;   "Unable to call Is3DLoaded - no native object bound to the script object"
; which ABORTS THE WHOLE EVENT at that line -- including the
; RegisterForSingleUpdate that keeps the poll alive, so the script is dead for
; the rest of the save.  Seen in the user's Papyrus log (2026-08-17) on the
; CharacterGen Blades equipment held by Glenroy and Renault.
;
; GetParentCell() is safe on an inventory item (it simply returns None), so
; testing that FIRST lets us skip the unsafe call entirely for held items.
;
; AND NOT Is3DLoaded() ALONE EITHER — the two requirements above are
; independent, and satisfying only the container one re-opens the self-disable
; deadlock.  A script whose own poll body calls Disable() (or that sits on an
; initially-disabled 0x800 ref) drops its 3D at that instant, so a 3D-gated
; re-arm never fires again and every later state of that script is unreachable.
;
; Nehrim MQ00LichtScript is the worked example: state 0 Disable()s the light,
; state 1 (five seconds later) is the only SetStage MQ00 2 in the plugin, and
; stage 2's result script is the only EnablePlayerControls.  With a 3D gate the
; quest pins at stage 1 and the player never regains control.
Bool Function SafeGameModeGate(ObjectReference akRef) Global
  If (akRef == None)
    Return False
  EndIf
  ; No parent cell => in a container / not placed in the world.  Never call
  ; Is3DLoaded() on such a reference.
  Cell parentCell = akRef.GetParentCell()
  If (parentCell == None)
    Return False
  EndIf
  ; Cell-scoped, matching TES4: an attached cell ticks its refs whether or not
  ; they are visible.  Is3DLoaded() is checked first only as a fast path.
  If (akRef.Is3DLoaded())
    Return True
  EndIf
  Return parentCell.IsAttached()
EndFunction

; ==========================================================================
; Actor Value Mapping (TES4 AV names → TES5 AV names)
; ==========================================================================

; SKYRIM HAS NO ATTRIBUTES. Strength, Intelligence, Willpower, Agility, Speed,
; Endurance, Personality and Luck do not exist as actor values, and no TES5
; actor value is a faithful stand-in — every candidate sits on a different
; scale, so comparing a 0-100 attribute threshold against one is arbitrary.
;
; These used to be aliased onto the nearest-looking AV (Strength->UnarmedDamage,
; Endurance->HealRate, Agility->SpeedMult, Personality->Speechcraft) and that
; silently broke every Morroblivion guild. The Fighters Guild advancement
; script gates each promotion on `Player.GetAV Strength >= 30 && Player.GetAV
; Endurance >= 30`; UnarmedDamage sits near 0 so the check could never pass at
; any level, while SpeedMult sits near 100 so the Thieves Guild's Agility gate
; passed unconditionally. Neither is the authored behaviour.
;
; IsTES4Attribute lets the readers below no-op instead: a read returns a value
; that satisfies any authored threshold (attribute gates cap at 100 in TES4 —
; the highest in the guild scripts is 35) so the gate falls open, and a write
; is discarded rather than corrupting a live Skyrim value. Falling open is the
; faithful outcome: an Oblivion attribute gate exists to keep an
; under-developed character out, and a Skyrim character has no way to raise an
; attribute at all, so enforcing it would lock the content away permanently
; rather than merely early.
Bool Function IsTES4Attribute(String avName) Global
  Return avName == "Strength" || avName == "Intelligence" || \
         avName == "Willpower" || avName == "Agility" || \
         avName == "Speed" || avName == "Endurance" || \
         avName == "Personality" || avName == "Luck"
EndFunction

; Value returned for a removed attribute. Above every authored TES4 attribute
; threshold (the ceiling is 100) so `>=` gates pass, and positive so the rarer
; `> 0` / `!= 0` forms behave the same way.
Float Function TES4AttributeStub() Global
  Return 100.0
EndFunction

String Function MapActorValue(String avName) Global
  ; Skills (renamed and/or merged in TES5). "Speechcraft" and "Marksman" are
  ; the engine's internal AV names for the skills Skyrim's UI calls Speech and
  ; Archery — both resolve; the UI names do not.
  If avName == "Armorer"
    Return "Smithing"
  ElseIf avName == "Athletics"
    Return "Stamina"
  ElseIf avName == "Blade"
    Return "OneHanded"
  ElseIf avName == "Blunt"
    Return "OneHanded"
  ElseIf avName == "HandToHand"
    Return "UnarmedDamage"
  ElseIf avName == "Mysticism"
    Return "Illusion"
  ElseIf avName == "Mercantile"
    Return "Speechcraft"
  ElseIf avName == "Security"
    Return "Lockpicking"
  ElseIf avName == "Acrobatics"
    Return "Stamina"
  ElseIf avName == "Fatigue"
    Return "Stamina"
  ElseIf avName == "Encumbrance"
    Return "CarryWeight"
  ElseIf avName == "Responsibility"
    Return "Morality"
  Else
    Return avName
  EndIf
EndFunction

Float Function GetTES4ActorValue(Actor akActor, String avName) Global
  If IsTES4Attribute(avName)
    Return TES4AttributeStub()
  EndIf
  Return akActor.GetActorValue(MapActorValue(avName))
EndFunction

Function SetTES4ActorValue(Actor akActor, String avName, Float afValue) Global
  If IsTES4Attribute(avName)
    Return
  EndIf
  akActor.SetActorValue(MapActorValue(avName), afValue)
EndFunction

Function ModTES4ActorValue(Actor akActor, String avName, Float afValue) Global
  If IsTES4Attribute(avName)
    Return
  EndIf
  akActor.ModActorValue(MapActorValue(avName), afValue)
EndFunction

Function ForceTES4ActorValue(Actor akActor, String avName, Float afValue) Global
  If IsTES4Attribute(avName)
    Return
  EndIf
  akActor.ForceActorValue(MapActorValue(avName), afValue)
EndFunction

; ==========================================================================
; Position / Angle Axis Helpers
; TES4: GetPos X → Papyrus: GetPositionX()
; ==========================================================================

Float Function GetPos(ObjectReference akRef, String axis) Global
  If axis == "X" || axis == "x"
    Return akRef.GetPositionX()
  ElseIf axis == "Y" || axis == "y"
    Return akRef.GetPositionY()
  ElseIf axis == "Z" || axis == "z"
    Return akRef.GetPositionZ()
  EndIf
  Return 0.0
EndFunction

Function SetPos(ObjectReference akRef, String axis, Float afValue) Global
  Float x = akRef.GetPositionX()
  Float y = akRef.GetPositionY()
  Float z = akRef.GetPositionZ()
  If axis == "X" || axis == "x"
    x = afValue
  ElseIf axis == "Y" || axis == "y"
    y = afValue
  ElseIf axis == "Z" || axis == "z"
    z = afValue
  EndIf
  akRef.SetPosition(x, y, z)
EndFunction

Float Function GetAngle(ObjectReference akRef, String axis) Global
  If axis == "X" || axis == "x"
    Return akRef.GetAngleX()
  ElseIf axis == "Y" || axis == "y"
    Return akRef.GetAngleY()
  ElseIf axis == "Z" || axis == "z"
    Return akRef.GetAngleZ()
  EndIf
  Return 0.0
EndFunction

Function SetAngle(ObjectReference akRef, String axis, Float afValue) Global
  Float x = akRef.GetAngleX()
  Float y = akRef.GetAngleY()
  Float z = akRef.GetAngleZ()
  If axis == "X" || axis == "x"
    x = afValue
  ElseIf axis == "Y" || axis == "y"
    y = afValue
  ElseIf axis == "Z" || axis == "z"
    z = afValue
  EndIf
  akRef.SetAngle(x, y, z)
EndFunction

; ==========================================================================
; Combat
; ==========================================================================

; TES4 StartCombat FORCES the fight: the actor attacks the target regardless
; of aggression, disposition or faction relations.  CharacterGen's finale
; depends on that -- the final assassin has base aggression 0, his only
; faction (MythicDawnCGAssassin) has no hostile relations, the Emperor's
; faction Friends it at +50 -- yet `CGAssassinFinal.startcombat
; UrielSeptimRef` must still cut the Emperor down.
;
; Skyrim's StartCombat is only a suggestion the combat AI re-evaluates at
; once: an actor with Aggression 0 exits combat immediately (vanilla's own
; turn-hostile fragment, MS08 "In My Time Of Need", pairs
; `MS08SaadiaFaction.SetEnemy(PlayerFaction)` with
; `SetAV("Aggression", 1)` for exactly this reason), and a target the actor
; has no hostile reaction to is dropped as invalid.
;
; So supply the two things the combat AI needs, then force it:
;   - floor Aggression at 1 ("attacks Enemies").  Tier 1 never widens to
;     neutrals, so this cannot make the actor attack bystanders.
;   - make the pair FACTION enemies through the two conversion-owned
;     factions the import writes for exactly this purpose
;     (TES4ForceCombatAttackers is record-side Enemy of
;     TES4ForceCombatVictims, both directions).  This is the vanilla
;     runtime-hostility idiom generalised to an arbitrary victim, and it
;     works for ANY actors.  The earlier attempt used relationship rank -4,
;     which only exists between UNIQUE actors — the CharacterGen final
;     assassin is non-unique, the rank write silently no-opped, and
;     StartCombat dropped the friendly Emperor as an invalid target.
;
; The memberships persist until death or an explicit stopcombat, matching
; TES4 StartCombat (fight until someone dies or a script stands them down).
; Cross-pair contamination (attacker A hostile to victim B forced in a
; different scene) is accepted: forced attackers are overwhelmingly scene
; actors that die in their scene, and TES4's own disposition damage from
; StartCombat leaked comparably.
Function ForceCombat(Actor akAttacker, Actor akTarget, Faction akAttackers, Faction akVictims) Global
  If akAttacker == None || akTarget == None
    Return
  EndIf
  If akAttackers != None && akVictims != None
    akAttacker.AddToFaction(akAttackers)
    akTarget.AddToFaction(akVictims)
  EndIf
  If akAttacker.GetActorValue("Aggression") < 1.0
    akAttacker.SetActorValue("Aggression", 1)
  EndIf
  akAttacker.StartCombat(akTarget)
EndFunction

; TES4 "PlayerFaction" converts to a plugin faction the RUNTIME player was
; never a member of — membership lives on Skyrim's own Player NPC (0x7),
; which the conversion does not touch.  So a scripted relation flip against
; the converted PlayerFaction reaches nobody.  Mirror those flips onto the
; vanilla PlayerFaction (Skyrim.esm 0x000DB1), the faction the real player
; actually belongs to.  aiMode: 1 = enemy, 0 = neutral, 2 = friend.
Function MirrorPlayerFactionRelation(Faction akOther, Int aiMode) Global
  If akOther == None
    Return
  EndIf
  Faction pf = Game.GetFormFromFile(0x000DB1, "Skyrim.esm") as Faction
  If pf == None
    Return
  EndIf
  If aiMode == 1
    akOther.SetEnemy(pf, false, false)
  ElseIf aiMode == 0
    ; Explicitly clearing a relation: SetEnemy with both "neutral" bools
    ; writes Neutral, the same idiom the faction-reaction conversion uses.
    akOther.SetEnemy(pf, true, true)
  Else
    akOther.SetAlly(pf, true, true)
  EndIf
EndFunction

; Oblivion's IsActorEvil is used as a hostility/crime guard by authored
; scripts.  Skyrim exposes the actor-to-player hostility decision directly.
Bool Function IsActorEvil(Actor akActor) Global
  Return akActor != None && akActor.IsHostileToActor(Game.GetPlayer())
EndFunction

; SameFactionAsPC asks whether the actor and player share any live faction
; membership.  SKSE64 exposes the full faction arrays, including memberships
; added or removed at runtime.
Bool Function SameFactionAsPC(Actor akActor) Global
  Return SameFaction(akActor, Game.GetPlayer())
EndFunction

; True when both actors currently share at least one faction. SKSE64 exposes
; the live faction array, so additions/removals made by scripts are included.
Bool Function SameFaction(Actor akActor, Actor akOther) Global
  If akActor == None || akOther == None
    Return False
  EndIf
  Faction[] theirs = akActor.GetFactions(-128, 127)
  Int i = 0
  While i < theirs.Length
    If akOther.IsInFaction(theirs[i])
      Return True
    EndIf
    i += 1
  EndWhile
  Return False
EndFunction

; TES4 ForceTakeCover starts a timed flee-from-target AI procedure. The
; helper activator owns one independent timer per invocation, so this call
; returns immediately and never stalls the authored event for the duration.
Function ForceTakeCover(Actor akActor, Actor akThreat, Float afDuration, Activator akTaskBase) Global
  If akActor == None || akThreat == None || akTaskBase == None
    Return
  EndIf
  TES4TakeCoverTask task = akActor.PlaceAtMe(akTaskBase, 1, True) as TES4TakeCoverTask
  If task != None
    task.BeginTask(akActor, akThreat, afDuration)
  EndIf
EndFunction

; Skyrim exposes no Papyrus time-of-death native.  Preserve TES4's game-hour
; result per actor in a spare actor-value slot: first observation is zero,
; later reads return elapsed game hours, and resurrection clears the stamp.
Float Function GetTimeDead(Actor akActor) Global
  If akActor == None
    Return 0.0
  EndIf
  If !akActor.IsDead()
    akActor.SetActorValue("Variable04", 0.0)
    Return 0.0
  EndIf
  Float now = Utility.GetCurrentGameTime()
  Float stamp = akActor.GetActorValue("Variable04")
  If stamp <= 0.0
    akActor.SetActorValue("Variable04", now)
    Return 0.0
  EndIf
  Return (now - stamp) * 24.0
EndFunction

; ==========================================================================
; Crime / Faction
; ==========================================================================

Function SetCrimeGold(Faction akFaction, Int aiGold) Global
  akFaction.SetCrimeGold(aiGold)
EndFunction

Int Function GetCrimeGold(Faction akFaction) Global
  Return akFaction.GetCrimeGold()
EndFunction

Function ModCrimeGold(Faction akFaction, Int aiGold) Global
  akFaction.ModCrimeGold(aiGold, false)
EndFunction

; ==========================================================================
; Sound Wrappers
; ==========================================================================

Function PlaySound3D(ObjectReference akSource, Sound akSound) Global
  akSound.Play(akSource)
EndFunction

; ==========================================================================
; Essential / Protected
; ==========================================================================

Function SetEssential(ActorBase akActorBase, Bool abEssential) Global
  akActorBase.SetEssential(abEssential)
EndFunction

Bool Function IsEssential(Actor akActor) Global
  Return akActor.GetActorBase().IsEssential()
EndFunction

; ==========================================================================
; Message Wrappers
; TES4 Message "text" → single-line notification
; TES4 MessageBox "text" "btn1" "btn2" → needs Message form (emit TODO)
; ==========================================================================

Function ShowNotification(String text) Global
  Debug.Notification(text)
EndFunction

Function ShowMessageBox(String text) Global
  Debug.MessageBox(text)
EndFunction

; ==========================================================================
; Lock Wrappers
; TES4: Lock 50 → Lock(true, 50)
; TES4: Unlock → Lock(false)
; ==========================================================================

Function LockAtLevel(ObjectReference akRef, Int aiLevel) Global
  akRef.Lock(true, aiLevel)
EndFunction

Function Unlock(ObjectReference akRef) Global
  akRef.Lock(false)
EndFunction

; ==========================================================================
; Ownership Wrappers
; ==========================================================================

Function SetOwnership(ObjectReference akRef, ActorBase akOwner) Global
  akRef.SetActorOwner(akOwner)
EndFunction

Function SetFactionOwnership(ObjectReference akRef, Faction akFaction) Global
  akRef.SetFactionOwner(akFaction)
EndFunction

; ==========================================================================
; AI Package Wrappers
; ==========================================================================

Function EvaluatePackage(Actor akActor) Global
  akActor.EvaluatePackage()
EndFunction

; ==========================================================================
; Container
; ==========================================================================

; TES4 `GetContainer` returns the container an item is inside (0 when it is
; lying in the world).  Papyrus has no way to walk from an item reference back
; to its container, but it does not need one to answer the question every
; caller actually asks: an item held in an inventory has no 3D placement, so
; its parent cell is None.  That is the same test, and it is exact.
Bool Function IsInContainer(ObjectReference akRef) Global
  Return akRef.GetParentCell() == None
EndFunction

; ==========================================================================
; Magic / Actor State
; ==========================================================================

; TES4 IsSpellTarget: "is this actor currently affected by spell X".  The
; converter resolves X to the Skyrim MGEF the imported spell actually carries
; and passes its Skyrim.esm FormID here.
Bool Function HasMagicEffectByID(Actor akActor, Int aiFormID) Global
  If akActor == None
    Return False
  EndIf
  MagicEffect fx = Game.GetFormFromFile(aiFormID, "Skyrim.esm") as MagicEffect
  If fx == None
    Return False
  EndIf
  Return akActor.HasMagicEffect(fx)
EndFunction

; TES4 GetIsCreature: Skyrim marks people with the ActorTypeNPC keyword
; (Skyrim.esm 0x00013794) on their race; converted creatures use generated
; races without it.
Bool Function GetIsCreature(Actor akActor) Global
  If akActor == None
    Return False
  EndIf
  Keyword npcKeyword = Game.GetFormFromFile(0x00013794, "Skyrim.esm") as Keyword
  If npcKeyword == None
    Return False
  EndIf
  Return !akActor.HasKeyword(npcKeyword)
EndFunction

; TES4 IsLeftUp reports which side of a knocked-down quadruped faces upward.
; Skyrim's creature behavior graph performs the same left/right pose match and
; exposes the selected child as iGetUpType (0 = GetUpLeft, 1 = GetUpRight).
Bool Function IsLeftUp(Actor akActor) Global
  If akActor == None
    Return False
  EndIf
  Return akActor.GetAnimationVariableInt("iGetUpType") == 0
EndFunction

; TES4 HasVampireFed: Skyrim's PlayerVampireQuest (Skyrim.esm 0x000EAFD5)
; tracks feeding — VampireStatus is 1 exactly while a vampire has recently fed
; (it climbs to 2..4 as the player goes hungry).
Bool Function HasVampireFed() Global
  Quest vq = Game.GetFormFromFile(0x000EAFD5, "Skyrim.esm") as Quest
  PlayerVampireQuestScript vs = vq as PlayerVampireQuestScript
  If vs == None
    Return False
  EndIf
  Return vs.VampireStatus == 1
EndFunction

; TES4 IsGuard: Skyrim guards are all members of GuardDialogueFaction
; (Skyrim.esm 0x0002BE3B).
Bool Function IsGuard(Actor akActor) Global
  If akActor == None
    Return False
  EndIf
  Faction guardFaction = Game.GetFormFromFile(0x0002BE3B, "Skyrim.esm") as Faction
  If guardFaction == None
    Return False
  EndIf
  Return akActor.IsInFaction(guardFaction)
EndFunction

; TES4 SetActorRefraction: no refraction control in Papyrus; a translucent
; alpha is the closest visual.  0 restores full opacity, anything else fades.
Function SetActorRefraction(Actor akActor, Float afValue) Global
  If akActor == None
    Return
  EndIf
  If afValue > 0.0
    akActor.SetAlpha(0.3, True)
  Else
    akActor.SetAlpha(1.0, True)
  EndIf
EndFunction

; TES4 (OBSE) ResetFallDamageTimer cleared the accumulated fall distance so the
; next landing did no damage.
;
; Skyrim has NO vanilla-Papyrus route to this.  The console command survives
; (opcode 4404) but is not bound to Papyrus; the GMST the fall-damage formula
; reads (fJumpFallHeightMin) has readers but no vanilla writer — SKSE's
; Game.SetGameSettingFloat does not compile against the vanilla headers this
; pipeline builds with, verified against the compiler; and the blunt
; alternatives (SetGhost, SetInvulnerable) suppress ALL damage, which would
; make a levitation scroll grant temporary immortality — a far worse defect
; than the one being fixed.
;
; So this keeps the ONE effect that is both faithful and scoped: heal the
; actor back up by the fall's cost.  DamageResist is applied for the window
; instead of invulnerability, so ordinary combat damage still lands.
;
; Callers are per-frame effect updates that stop when the effect ends, so the
; resistance is (re)applied on each call and RestoreFallDamage removes it —
; the paired on/off contract in docs/papyrus_conversion_notes.md.  The
; modifier is tracked so repeated calls cannot stack it without bound.
Function SuppressFallDamage(Actor akActor = None) Global
  If akActor == None
    akActor = Game.GetPlayer()
  EndIf
  If akActor == None
    Return
  EndIf
  ; ForceActorValue, not Mod: this runs every update tick, and a modifier
  ; would otherwise accumulate for as long as the effect lasts.
  akActor.ForceActorValue("DamageResist", 10000.0)
EndFunction

; Undo SuppressFallDamage.  Emitted by the effect-finish path of any script
; that called it; also safe to call blind.
Function RestoreFallDamage(Actor akActor = None) Global
  If akActor == None
    akActor = Game.GetPlayer()
  EndIf
  If akActor == None
    Return
  EndIf
  akActor.ForceActorValue("DamageResist", 0.0)
EndFunction

; ==========================================================================
; Day/Time Helpers
; ==========================================================================

; Every function here is Global, so none of them may touch a script property —
; a Global has no instance to read one from ("variable GameDaysPassed is
; undefined").  Fetch the vanilla GameDaysPassed global (Skyrim.esm 0x00000039)
; by form ID instead.
Int Function GetDayOfWeek() Global
  GlobalVariable daysPassed = Game.GetFormFromFile(0x00000039, "Skyrim.esm") as GlobalVariable
  If daysPassed == None
    Return 0
  EndIf
  Return ((daysPassed.GetValue() as Int) % 7)
EndFunction

Float Function GetCurrentTime() Global
  Return Utility.GetCurrentGameTime()
EndFunction

; ==========================================================================
; Math
; ==========================================================================

; OBSE's `exp`/`log` have no Papyrus native (Math.psc ships sin/cos/tan/asin/
; acos/atan/sqrt/pow/abs/Floor/Ceiling and nothing else), so they are built on
; Math.pow here.  Morrowind_ob's levitation code is the heavy user: its damping
; term is `set dampNorm to exp dampExp`, evaluated every frame.
Float Function Exp(Float afValue) Global
  Return Math.pow(2.718281828, afValue)
EndFunction

; Natural log via the change-of-base identity ln(x) = log2(x) / log2(e).
; Papyrus has no log of any base either, so log2 is computed by binary
; decomposition: pull out the integer power of two, then refine the fraction.
Float Function Log(Float afValue) Global
  If afValue <= 0.0
    Return 0.0  ; ln is undefined for x <= 0; callers treat 0 as "no contribution"
  EndIf
  Float x = afValue
  Float log2 = 0.0
  While x >= 2.0
    x /= 2.0
    log2 += 1.0
  EndWhile
  While x < 1.0
    x *= 2.0
    log2 -= 1.0
  EndWhile
  ; x is now in [1,2): refine 16 fractional bits of log2(x).
  Float frac = 0.5
  Int i = 0
  While i < 16
    x *= x
    If x >= 2.0
      x /= 2.0
      log2 += frac
    EndIf
    frac /= 2.0
    i += 1
  EndWhile
  Return log2 / 1.442695041  ; 1/ln(2)
EndFunction

; ==========================================================================
; 3D / Model refresh
; ==========================================================================

; OBSE `ref.Update3D` rebuilds a reference's 3D after its model changed —
; Morrowind_ob calls it through the fbmwUpdate3D helper after swapping the
; player's skeleton for the werewolf one.  Papyrus has no direct equivalent
; (QueueNiNodeUpdate is SKSE), but disable/enable tears the 3D down and
; rebuilds it, which is what the call is for.  The reference must be re-enabled
; even if it was already disabled — callers only ever use this on visible
; actors, and leaving one disabled would delete it from the world.
Function Update3D(ObjectReference akRef) Global
  If akRef == None
    Return
  EndIf
  akRef.Disable()
  akRef.Enable()
EndFunction

; ==========================================================================
; Plugin detection
; ==========================================================================

; OBSE `IsModLoaded "Foo.esp"` asks whether a plugin is in the load order.
; Vanilla Papyrus has no direct query, but Game.GetFormFromFile returns None
; for a file that is not loaded, so asking it for the plugin's own header
; record (0x00000000 in that file's local space) answers the same question.
Bool Function IsModLoaded(String asPlugin) Global
  Return Game.GetFormFromFile(0x00000000, asPlugin) != None
EndFunction

; ==========================================================================
; Breakaway props
; ==========================================================================

; Oblivion authors break-apart props (mwallplankbreakaway01's planks,
; IDCrumbleWall01's bricks) as KEYFRAMED bodies that carry real mass and
; `Unyielding = 1`.  The animation only creaks the pieces off their mounting --
; the planks rotate 15.19 degrees and have ZERO translation keys -- and the
; visible break is HAVOK taking over: the pieces detach and fall.
;
; Skyrim keyframed bodies never yield to gravity, so a straight conversion left
; the planks hanging in the half-broken pose forever.  Shipping them dynamic in
; the NIF instead was also wrong -- they dropped the moment the cell loaded,
; before the clip had played.  So the mesh keeps them keyframed (held, following
; the clip, exactly like Unyielding) and the release happens HERE, once the clip
; has run.
;
; The wait covers the clip.  Converted breakaway `Unequip` sequences run 0.033s
; to 3.8s (median 0.033; only 4 of 27 exceed 0.5s), and Papyrus cannot query a
; Gamebryo sequence's length -- PlayAnimationAndWait never returns for a
; BGSGamebryoSequenceGenerator state, and the graph declares no `end` event to
; wait on.  One second covers every clip but the 3.8s outlier while still
; reading as "it gave way, then it fell".
;
; Inert on anything that is not a breakaway piece: every other animated object
; converts to a mass-0 keyframed body, and a mass-0 body has infinite effective
; mass, so going dynamic cannot make it fall.  Doors, gates and portcullises
; driven by the same animation group are unaffected.
Function ReleaseBreakaway(ObjectReference akRef) Global
  If akRef == None
    Return
  EndIf
  Utility.Wait(1.0)
  ; Motion_Dynamic = 1.  abAllowActivate must be true or the body stays asleep
  ; and never starts simulating.
  akRef.SetMotionType(1, true)
EndFunction

; SetDestroyed(1) deferred until the clip that preceded it has finished.
;
; TES4 pairs `playgroup <grp>` with `setDestroyed 1` on the very next line
; (CTrigTripwire01SCRIPT, CTrapLogs01SCRIPT, CTrapCaveIn01SCRIPT,
; MPlanksBreakAway01Script).  In Oblivion that was harmless: with no
; destruction data on the record, setDestroyed only stopped the object being
; activated again.  Oblivion ships ZERO DEST subrecords, so nothing we convert
; has a destroyed state either -- but Skyrim's SetDestroyed still RESETS THE
; REFERENCE'S 3D, and doing that one line after PlayAnimation tore down the
; NiControllerSequence before a single frame of it had been drawn.  That is
; what stopped the tripwire visibly snapping when it was walked over.
;
; Waiting first preserves both halves of the original intent: the break
; animation plays to completion, and the object still ends up destroyed so it
; cannot fire a second time.  Same 1.0s budget as ReleaseBreakaway, chosen the
; same way -- Papyrus cannot query a Gamebryo sequence's length, and every
; converted break clip but one outlier finishes well inside it.
Function DestroyAfterAnimation(ObjectReference akRef, FormList akDestroyed) Global
  If akRef == None
    Return
  EndIf
  Utility.Wait(1.0)
  ; Through the mirroring setter, never the bare native: a converted
  ; `getdestroyed` reads the FormList (see SetDestroyed/GetDestroyed below).
  SetDestroyed(akRef, akDestroyed)
EndFunction

; ==========================================================================
; Spoken lines: TES4 `set T to [ref.]Say[To] [target,] Topic`
; ==========================================================================
;
; TES4's Say/SayTo were SYNCHRONOUS: the engine picked the INFO, started the
; audio and RETURNED ITS LENGTH before the next script line ran, so a polled
; conversation is written as
;
;     if speaker == 4 && convTimer <= 0
;         set convTimer to SayTo player, CharGenMain     ; := line length
;     endif
;
; and every other participant waits on the same countdown.  Papyrus Say() is
; fire-and-forget and returns nothing, so the length has to come from the
; engine's OWN signal that the line is under way: the INFO's OnBegin fragment.
; Every converted INFO carries a Begin+End fragment whose fixed job is to call
; LineBegan / LineEnded here; the state lives in script Actor Values ON THE
; SPEAKER, so no property binding and no per-quest owner analysis is needed:
;
;     Variable03  real time this speaker's last End fragment ran (grace stamp)
;     Variable04  running average of this speaker's End overhead (adaptive tail)
;     Variable06  real time the current line began (diagnostics)
;     Variable07  claim token while a SayLine is in progress for this speaker
;     Variable08  claim deadline (game time, days) - a stale claim expires
;     Variable09  length of the line now playing (0 = not speaking)
;     Variable10  speaking deadline (game time) - a lost End fragment expires
;   and on the PLAYER, Variable05/06 = hi/lo halves of the FormID of the last
;   actor to speak a line inside the player's dialogue menu (PlayerIsInDialogue)
;   and Variable04 = the game-wide End-overhead average, and Variable07 =
;   the real time the line now playing ANYWHERE ends (_OtherLineInProgress:
;   Skyrim refuses a Say while another actor is mid-line).
;   🛑 NO Debug.Trace ON THIS PATH.  Every SayLine / LineBegan / LineEnded used
;   to write a "TES4Say ..." trace.  Papyrus builds the whole concatenated
;   string -- including the Utility.GetCurrentRealTime() calls and the
;   IsInCombat/IsWeaponDrawn/IsAlerted queries inside it -- BEFORE Debug.Trace
;   decides whether logging is even enabled, so the cost was paid on every
;   line whether or not the user had Papyrus logging on.  Removed 2026-08-17
;   while hunting the per-line stutter.  If a Say path ever needs tracing
;   again, gate it behind a compile-time flag so a shipping build pays nothing.
;
; SayLine restores the TES4 contract exactly: it BLOCKS until the engine has
; begun the line, then returns THAT LINE'S REAL LENGTH AND NOTHING MORE, and
; the caller's script goes on immediately - the countdown, the `speaker`
; handoff and any `set convTimer to convTimer + 2` pause the End result adds
; all behave as they did in Oblivion.  The End-overhead grace that used to be
; added to the return value is held in _IsSpeaking instead, so it is paid only
; by a re-Say on the same actor and never by a handoff to the next speaker.
; A Say nothing under the topic qualifies for returns 0 after SAY_START_WAIT,
; and the caller's own poll simply retries - which is what Oblivion did too.
;
; Waits, in order:
;   * the speaker is in the player's dialogue menu -> wait (Oblivion froze
;     GameMode while any menu was open; a Say on an actor in dialogue is lost
;     or, per the CK wiki, can crash);
;   * the speaker is still speaking a tracked line -> wait for its End
;     (Oblivion cut the line; Skyrim silently DROPS the new Say instead, and
;     with it the result script that advances the scene);
;   * one waiter per speaker: a second SayLine while one is pending returns
;     a short backoff instead of queueing a duplicate.

; Seconds added to the returned line length.
;
; ð THE TAIL IS NOT DEAD AIR ANY MORE.  It used to be added to the value
; SayLine returns, i.e. charged to the CALLER'S COUNTDOWN, so every line was
; followed by a silence of one tail before the script even looked for the next
; line.  Measured 2026-08-16 (temp/chargen_rec_5.log, 90 transitions): of the
; 31 audible gaps, 26 handed off to a DIFFERENT actor -- median 1.49s -- and
; for those the tail buys nothing at all, because it exists only to stop the
; SAME actor being re-Said while the engine still counts him as talking.
;
; So the tail is no longer returned.  It is enforced where it is actually
; needed instead: _IsSpeaking() holds a re-Say off until this speaker's End
; fragment has run plus the measured overhead (see the grace check there), and
; SayLine's pre-wait blocks on that.  A handoff to another actor pays nothing.
;
; The overhead is stable per machine but not knowable in advance (measured
; 2026-08-16 on the user's setup: median 0.37s, p90 0.54s single-response;
; 11-24s under a starved VM), so it is MEASURED: LineEnded records each
; line's overhead into a per-speaker running average (Variable04) and a
; global one on the player.
; How long SayLine waits for the engine's OnBegin fragment before declaring
; the line dropped.  This BOUNDS how long a SayLine call can block, so the
; say-timer pre-charge the converter emits must outlast it -- converter.py
; keeps SAY_START_WAIT in step.  Nominal: the engine begins a line it accepts
; within ~0.15-0.26s (measured), and each iteration is a VM turn, so under
; load the real wait stretches with everything else.
; ---------------------------------------------------------------- tracing --
;
; Say-path diagnostics, OFF in every shipped build.
;
; 🛑 NEVER call Debug.Trace directly on the Say path.  Papyrus evaluates a
; call's arguments BEFORE the callee decides to discard them, so a trace like
;   Debug.Trace("... " + _Who(a) + " ... " + Utility.GetCurrentRealTime())
; pays for the FormID lookup, the native time query and the whole string
; concatenation on every line even when Papyrus logging is disabled.  Six such
; traces used to sit in SayLine/LineBegan/LineEnded and ran on every spoken
; line in the game.
;
; Routing them through this one function makes the cost a single Bool test
; against a constant, which the compiler folds away when SAY_TRACE() is False.
; Flip SAY_TRACE to True ONLY for a local diagnostic build.
Bool Function SAY_TRACE() Global
  Return False
EndFunction

Function _SayTrace(String asTag, Float afValue) Global
  If SAY_TRACE()
    Debug.Trace("TES4Say " + asTag + " " + afValue)
  EndIf
EndFunction

Float Function SAY_START_WAIT() Global
  Return 1.5
EndFunction

; How long to wait for the engine to BEGIN a line before treating it as
; refused.  Measured: an accepted line begins in 0.15-0.31s (n=76, 2026-08-16),
; so 0.4s covers every accepted case with margin while cutting the cost of a
; refusal from 1.5s to 0.4s.  That matters because a caller waiting on a
; refused line also blocks the actor whose turn it really is -- see the loop in
; SayLine for the measured case (Glenroy's "Usual mixup with the Watch").
Float Function SAY_ACCEPT_WAIT() Global
  Return 0.4
EndFunction
Float Function SAY_TAIL_MIN() Global
  Return 0.35
EndFunction
Float Function SAY_TAIL_MAX() Global
  Return 2.5
EndFunction
Float Function SAY_TAIL_MARGIN() Global
  Return 0.2
EndFunction
Float Function SAY_TAIL_DEFAULT() Global
  Return 0.8       ; until anything has been measured
EndFunction

; The tail for this speaker's next line: its own measured End overhead if it
; has one, else the game-wide one, else the default -- plus the margin.
Float Function _TailFor(Actor a) Global
  Float est = 0.0
  If a != None
    est = a.GetActorValue("Variable04")
  EndIf
  If est <= 0.0
    est = Game.GetPlayer().GetActorValue("Variable04")
  EndIf
  If est <= 0.0
    Return SAY_TAIL_DEFAULT()
  EndIf
  Float tail = est + SAY_TAIL_MARGIN()
  If tail < SAY_TAIL_MIN()
    tail = SAY_TAIL_MIN()
  ElseIf tail > SAY_TAIL_MAX()
    tail = SAY_TAIL_MAX()
  EndIf
  Return tail
EndFunction

; NON-BLOCKING SayLine, for callers on the ENGINE'S DISPATCH PATH.
;
; ð NEVER BLOCK A FRAGMENT OR AN ENGINE CALLBACK.  SayLine waits for the
; engine's OnBegin fragment, which takes 0.18s median but up to SAY_START_WAIT
; (1.5s) when the line is refused.  In an OnUpdate poll that wait costs only
; that script's own tick.  In a QUEST STAGE FRAGMENT, an INFO fragment, or an
; OnPackageEnd / OnPackageStart / OnHit / OnCombatStateChanged callback it
; stalls the engine's own dispatch -- the stage transition, the package swap
; or the hit reaction cannot complete until the Say resolves.  That is the
; "massive stutter as a new line starts" the user reported: it accompanies a
; STAGE CHANGE (CharacterGen's Fragment_Stage_0016 / _0044 both blocked), not
; an ordinary polled line, which is why only some lines stutter.
;
; So on those paths we fire the line and DO NOT WAIT.  The countdown gets the
; caller's authored fallback (the topic's measured longest response, supplied
; by the converter) instead of the engine's exact length.  The speaker is
; still claimed and still tracked by LineBegan/LineEnded, so nothing can
; re-Say over the line -- only the RETURNED length is an estimate rather than
; a measurement, and only for these few sites.
Float Function SayLineNoWait(ObjectReference akSpeaker, Topic akTopic, Float afFallbackLength) Global
  If akSpeaker == None || akTopic == None
    Return afFallbackLength
  EndIf
  Actor a = akSpeaker as Actor
  If a == None || (a as Form).GetFormID() == 0x14
    akSpeaker.Say(akTopic)
    Return afFallbackLength
  EndIf
  ; Respect a line already in flight exactly as SayLine does: issuing a Say
  ; over a live line drops it AND loses that line's End result.
  If a.IsInDialogueWithPlayer() || _IsSpeaking(a)
    Return 0.5   ; busy: the caller's poll retries, same as a contended SayLine
  EndIf
  a.SetActorValue("Variable09", 0.0)
  a.Say(akTopic)
  Return afFallbackLength
EndFunction


Float Function SayLine(ObjectReference akSpeaker, Topic akTopic, Float afFallbackLength) Global
  Actor a = akSpeaker as Actor
  If a == None || akTopic == None || (a as Form).GetFormID() == 0x14
    ; Not an actor we can track (a talking activator, the player): open loop.
    If akSpeaker != None && akTopic != None
      akSpeaker.Say(akTopic)
    EndIf
    Return afFallbackLength
  EndIf
  Float now = Utility.GetCurrentGameTime()
  If a.GetActorValue("Variable07") > 0.0 && now < a.GetActorValue("Variable08")
    Return 0.5   ; another SayLine already owns this speaker's next line; poll again shortly
  EndIf
  ; Claim the speaker.  SetActorValue lands on the game thread a frame later,
  ; so two callers arriving in the same frame both read "free" above; the
  ; token + re-read after a frame lets exactly one of them keep the claim.
  ;
  ; The re-read only needs ONE FRAME to have passed, and Utility.Wait(0.0)
  ; yields exactly that.  It used to be Wait(0.05), which yields for at least
  ; 50ms and was paid on EVERY line -- part of the per-line cost the
  ; 2026-08-16 recording measured as a 0.73s median between a line's End and
  ; the next Say being issued.
  Float token = Utility.RandomFloat(1.0, 1000000.0)
  Float claimDays = _GameDays(5.0)   ; a claim not renewed for 5s is stale
  a.SetActorValue("Variable07", token)
  a.SetActorValue("Variable08", now + claimDays)
  Utility.Wait(0.0)
  If a.GetActorValue("Variable07") != token
    Return 0.5
  EndIf
  ; Wait out the player's dialogue menu and any line still playing.
  ; Wait for the speaker to be free.  _IsSpeaking covers BOTH a live line and
  ; the post-End grace, so there is no separate settle wait any more: the old
  ; flat Utility.Wait(0.25) after every busy wait was pure dead air on top of
  ; a condition that is now exact.  0.05 steps because the loop exits on an
  ; AV stamp, not on a race with the fragment.
  ; The other-speaker wait is CAPPED.  _IsSpeaking on our own actor may wait
  ; as long as it likes (that line is ours and its End is coming), but another
  ; actor's line is only a reason to hold off, never a reason to stall: cap it
  ; at SAY_START_WAIT so this can never cost more than the drop it replaces,
  ; and so one unrelated ambient line cannot hold up a whole conversation.
  Float otherCap = SAY_ACCEPT_WAIT()
  Float waited = 0.0
  While waited < 600.0 && (a.IsInDialogueWithPlayer() || _IsSpeaking(a) \
                           || (waited < otherCap && _OtherLineInProgress()))
    Utility.Wait(0.05)
    waited += 0.05
    a.SetActorValue("Variable08", Utility.GetCurrentGameTime() + claimDays)
  EndWhile
  ; Request the line and wait for the engine to begin it (LineBegan stores
  ; the length in Variable09).
  a.SetActorValue("Variable09", 0.0)
  Float t0 = Utility.GetCurrentRealTime()
  _SayTrace("SAY", 0.0)
  a.Say(akTopic)
  ; The engine begins a line it ACCEPTS within 0.15-0.31s (measured
  ; 2026-08-16, n=76: med 0.15, max 0.31).  Anything still silent well past
  ; that was refused, and every further tick is pure dead air on a line that
  ; will never play -- while it also blocks the speaker whose turn it
  ; genuinely is, because a Say is refused while another actor is mid-line.
  ;
  ; 🛑 THIS COSTS THE REAL NEXT SPEAKER, NOT JUST THE CALLER.  Measured in
  ; temp/chargen_rec_5.log: Renault's line ended at 118.386 and his own poll
  ; re-fired at 118.364 -- the turn had passed to Glenroy, but `speaker` is
  ; handed over inside the End FRAGMENT, so for the ~0.4s until that ran
  ; Renault's `speaker == 2 && convTimer <= 0` guard was still open.  His
  ; doomed request then sat until 120.828 and Glenroy's "Usual mixup with the
  ; Watch" was held to a 2.35s gap when its neighbours were 0.3-0.8s.
  ;
  ; So the wait scales to what an accepted line actually needs.  A dropped
  ; line now costs ~0.4s instead of 1.5s.  SAY_START_WAIT stays the bound the
  ; pre-charge is sized against, because a caller may still block that long
  ; when the VM is starved -- this only stops us WAITING OUT a refusal.
  Float t = 0.0
  While t < SAY_ACCEPT_WAIT() && a.GetActorValue("Variable09") == 0.0
    Utility.Wait(0.05)
    t += 0.05
  EndWhile
  Float len = a.GetActorValue("Variable09")
  a.SetActorValue("Variable07", 0.0)
  If len <= 0.0
    Return 0.0   ; dropped: nothing under the topic qualified (or the engine refused it)
  EndIf
  If len < 0.02
    len = afFallbackLength   ; began, but the line has no measured voice file
  EndIf
  ; ð LENGTH ONLY -- no tail.  TES4's Say returned the line's length and
  ; nothing more, and the caller's countdown is meant to expire when the audio
  ; does.  Adding the tail here padded EVERY line with a silence that only a
  ; same-actor re-Say ever needed; _IsSpeaking's grace window enforces that
  ; case directly.
  Return len
EndFunction

; OnBegin fragment hook: the engine has selected this INFO and started it.
Function LineBegan(ObjectReference akSpeakerRef, Float afLength) Global
  Actor a = akSpeakerRef as Actor
  If a == None
    ; A talking activator (a speak-as speaker, see SpeakAs): no actor values of
    ; its own, so the length lands on the PLAYER for SpeakAsLine to read,
    ; and the game-wide "a line is playing until" record is stamped as for
    ; any speaker.  The player's own Variable09 is otherwise unused (the
    ; player is never a tracked speaker: see the 0x14 tests below).
    If akSpeakerRef != None
      Actor gp0 = Game.GetPlayer()
      Float len0 = afLength
      If len0 <= 0.0
        len0 = 0.01
      EndIf
      gp0.SetActorValue("Variable09", len0)
      Float until0 = Utility.GetCurrentRealTime() + len0
      If until0 > gp0.GetActorValue("Variable07")
        gp0.SetActorValue("Variable07", until0)
      EndIf
    EndIf
    Return
  EndIf
  If (a as Form).GetFormID() == 0x14
    Return
  EndIf
  Float len = afLength
  If len <= 0.0
    len = 0.01                 ; unknown length: still marks "speaking"
  EndIf
  _SayTrace("BEGIN", afLength)
  a.SetActorValue("Variable09", len)
  a.SetActorValue("Variable03", 0.0)   ; a live line supersedes the End grace
  ; Game-wide "a line is in progress" record, kept on the PLAYER so any
  ; SayLine can consult it without knowing the other participants.  Skyrim
  ; refuses a Say while ANOTHER actor is mid-line, and the caller then burns
  ; the whole SAY_START_WAIT before finding out (measured 2026-08-16: 13 of
  ; 17 drops in temp/chargen_rec_5.log happened with a DIFFERENT actor
  ; speaking and the dropped actor silent).  Storing the deadline lets the
  ; pre-wait below hold off instead of being refused.
  Actor gp = Game.GetPlayer()
  Float until = Utility.GetCurrentRealTime() + len
  If until > gp.GetActorValue("Variable07")
    gp.SetActorValue("Variable07", until)
  EndIf
  ; Speaking deadline: VERY generous.  It only exists so a LOST End (actor
  ; killed or unloaded mid-line) cannot strand the speaker as busy forever;
  ; a late one must always hold a re-Say off.  Measured 2026-08-16 under a
  ; starved VM (start of CharacterGen): End fragments of 1-2s lines ran
  ; 11-17s late, a 10s margin expired first, and the speaker's own poll
  ; re-Said the line ("Yessir" twice).
  Float bound = afLength
  If bound <= 0.0
    bound = 10.0
  EndIf
  a.SetActorValue("Variable10", Utility.GetCurrentGameTime() + _GameDays(bound + 30.0))
  a.SetActorValue("Variable06", Utility.GetCurrentRealTime())
  ; A line spoken IN THE PLAYER'S DIALOGUE MENU: remember the speaker on the
  ; player, so PlayerIsInDialogue() can ask that actor whether the menu is
  ; still open (Skyrim has no direct "is the player in dialogue" query).
  If akSpeakerRef.IsInDialogueWithPlayer()
    Int fid = (akSpeakerRef as Form).GetFormID()
    Actor p = Game.GetPlayer()
    p.SetActorValue("Variable05", Math.Floor(fid / 65536) as Float)
    p.SetActorValue("Variable06", (fid - Math.Floor(fid / 65536) * 65536) as Float)
  EndIf
EndFunction

; OnEnd fragment hook: the line (all of its responses) has finished -- or was
; cut.  Clears the speaking flag ONLY if it still belongs to THIS line.
;
; The player can skip a menu line (click through the greeting) or exit the
; menu; the skipped line's End fragment and the next line's Begin fragment
; then run in the same frame, and End can land SECOND.  An unconditional
; clear then wiped the flag of the line that had just started; the speaker's
; own poll saw him idle, its Say() INTERRUPTED the live line, and that line's
; End result -- CharGenEmperor09's `setstage 43` -- was lost (measured
; 2026-08-16, three of three runs showed the ordering, one soft-locked).  The
; fragment knows its own length, so match on it: a mismatch means a newer
; line owns the flag and it is left alone.
Function LineEnded(ObjectReference akSpeakerRef, Float afLength = -1.0) Global
  Actor a = akSpeakerRef as Actor
  If a == None
    ; Talking activator (speak-as): release the game-wide record and the length.
    If akSpeakerRef != None
      Actor gp0 = Game.GetPlayer()
      gp0.SetActorValue("Variable09", 0.0)
      gp0.SetActorValue("Variable07", 0.0)
    EndIf
    Return
  EndIf
  If (a as Form).GetFormID() == 0x14
    Return
  EndIf
  _SayTrace("END", afLength)
  Float began = a.GetActorValue("Variable06")
  Float cur = a.GetActorValue("Variable09")
  Bool mine = afLength < 0.0 || Math.abs(cur - afLength) < 0.006 || (afLength <= 0.0 && cur <= 0.02)
  Float actual = Utility.GetCurrentRealTime() - began
  If mine
    a.SetActorValue("Variable09", 0.0)
    ; Stamp the moment this line's End ran.  _IsSpeaking treats the next
    ; _TailFor(a) seconds as still-speaking, which is where the old SAY_TAIL
    ; padding now lives -- charged only to a re-Say on THIS actor, never to a
    ; handoff.
    a.SetActorValue("Variable03", Utility.GetCurrentRealTime())
    ; Release the game-wide record NOW.  It was stamped optimistically as
    ; began+length; a line the player skips ends early, and holding the next
    ; speaker off for audio that already stopped would be dead air.
    Game.GetPlayer().SetActorValue("Variable07", 0.0)
    ; Learn this machine's End overhead from a line that played through
    ; (a skipped/cut line ends early and says nothing about the overhead).
    Float over = actual - afLength
    If afLength > 0.02 && over >= 0.0 && over < 5.0
      Float prev = a.GetActorValue("Variable04")
      If prev <= 0.0
        a.SetActorValue("Variable04", over)
      Else
        a.SetActorValue("Variable04", prev * 0.6 + over * 0.4)
      EndIf
      Actor p = Game.GetPlayer()
      prev = p.GetActorValue("Variable04")
      If prev <= 0.0
        p.SetActorValue("Variable04", over)
      Else
        p.SetActorValue("Variable04", prev * 0.8 + over * 0.2)
      EndIf
    EndIf
  EndIf
EndFunction

; True while the player is in a dialogue menu with anyone -- Oblivion's
; GameMode never ran then, so converted actor polls skip their pass.  Called
; every poll tick by every actor script, so it must be CHEAP: two AV reads
; when nobody has stamped a dialogue speaker, and the stamp is cleared as
; soon as that speaker reports the menu closed, so the GetForm +
; IsInDialogueWithPlayer pair only runs while a dialogue is actually open.
Bool Function PlayerIsInDialogue() Global
  Actor p = Game.GetPlayer()
  Float hi = p.GetActorValue("Variable05")
  Float lo = p.GetActorValue("Variable06")
  If hi <= 0.0 && lo <= 0.0
    Return False
  EndIf
  If hi >= 32768.0
    p.SetActorValue("Variable05", 0.0)   ; a runtime-created (FF) reference: cannot be rebuilt as an Int
    p.SetActorValue("Variable06", 0.0)
    Return False
  EndIf
  ObjectReference r = Game.GetForm((hi as Int) * 65536 + (lo as Int)) as ObjectReference
  If r != None && r.IsInDialogueWithPlayer()
    Return True
  EndIf
  ; A Goodbye reply keeps playing after the menu closes (and the player can
  ; leave mid-line): Oblivion's menu stayed up until the line was over, so
  ; hold the polls until the last dialogue speaker has finished it.
  Actor ra = r as Actor
  If ra != None && _IsSpeaking(ra)
    Return True
  EndIf
  p.SetActorValue("Variable05", 0.0)
  p.SetActorValue("Variable06", 0.0)
  Return False
EndFunction

; True while a re-Say on THIS actor would be dropped or would cut a live line.
;
; Two states count as speaking:
;   * a tracked line is playing (Variable09 > 0), bounded by the lost-End
;     deadline in Variable10;
;   * the line's End fragment has JUST run and the engine still counts him as
;     talking -- the grace window.  Variable03 holds the real time the End
;     fragment finished, and the tail (this actor's measured End overhead) is
;     how long after that a Say is still refused.
;
; The grace is what SAY_TAIL used to buy by padding the caller's countdown.
; Holding it HERE instead means only a re-Say on the same actor pays it; a
; handoff to a different speaker starts immediately.  That was 26 of the 31
; audible gaps in the 2026-08-16 recording.
; True while ANY tracked line is still playing anywhere.
;
; Skyrim refuses a Say issued while another actor is mid-line: the engine
; drops it silently, so SayLine sits out its whole SAY_START_WAIT and returns
; 0.0, and the caller's poll retries a tick later.  That is the 2-3s cluster
; of gaps -- measured 2026-08-16, 13 of 17 drops in temp/chargen_rec_5.log
; had a DIFFERENT actor speaking while the dropped actor was silent, e.g.
;   90.56 drop Emperor  | others speaking: Renault
;   95.29 drop Renault  | others speaking: Emperor
; the conversation relay racing itself, because each participant's guard is
; `speaker == N && convTimer <= 0` and convTimer counts the AUDIO length --
; it reaches zero while the previous speaker's End fragment has yet to run.
;
; Waiting is strictly better than being refused: the wait ends the moment the
; other line finishes, whereas a refusal costs the full timeout AND a retry.
Bool Function _OtherLineInProgress() Global
  Float until = Game.GetPlayer().GetActorValue("Variable07")
  If until <= 0.0
    Return False
  EndIf
  Return Utility.GetCurrentRealTime() < until
EndFunction

Bool Function _IsSpeaking(Actor a) Global
  If a.GetActorValue("Variable09") > 0.0 && Utility.GetCurrentGameTime() < a.GetActorValue("Variable10")
    Return True
  EndIf
  Float ended = a.GetActorValue("Variable03")
  If ended <= 0.0
    Return False
  EndIf
  Return (Utility.GetCurrentRealTime() - ended) < SAY_GRACE()
EndFunction

; How long after a line's End fragment a re-Say on the SAME actor is still
; refused.
;
; ð MEASURED, NOT GUESSED.  This used to be _TailFor(a) -- the full
; End-overhead average plus margin, about 0.57s -- inherited from when the
; same number padded the caller's countdown.  The 2026-08-16 recording says
; that is far too large: across 59 same-actor transitions the next Say
; succeeded as little as 0.00s after LineEnded and NOT ONE was dropped.  Once
; the End fragment has run the engine accepts the next line immediately.
;
; The reason the old SAY_TAIL genuinely mattered is a different one: it was on
; the CALLER'S TIMER, so a late End let the countdown expire while the line
; was still playing and the poll re-Said it (the duplicated "Yessir").  That
; case is now covered directly by Variable09 + the Variable10 deadline, which
; are exact.  So this only has to bridge the frame between LineEnded running
; and the engine's own talking flag clearing.
Float Function SAY_GRACE() Global
  Return 0.05
EndFunction


; Real seconds -> game-time days at the current TimeScale.  Deadlines are kept
; in GAME time because GetCurrentRealTime restarts with the process: a stamp
; saved in one session compares against a different clock in the next.
Float Function _GameDays(Float afSeconds) Global
  GlobalVariable ts = Game.GetFormFromFile(0x0000003A, "Skyrim.esm") as GlobalVariable
  Float scale = 20.0
  If ts != None && ts.GetValue() > 0.0
    scale = ts.GetValue()
  EndIf
  Return afSeconds * scale / 86400.0
EndFunction

; ============================================================= speak-as ==
;
; TES4 `Say <topic> <force-subtitles> <speak-as NPC> [<in-head>]` -- a line
; spoken THROUGH a marker, shrine or door AS some NPC.  Skyrim's Say has no
; speak-as argument and keys voice lookup on the SPEAKER, and a bare XMarker
; STAT has no voice type at all, so the engine finds no voice folder and
; plays nothing.
;
; The importer gives each such call site a talking activator (TACT) carrying
; that NPC's voice type, placed at the emitter's authored position
; (tes5_import/speaker_activators.py).  Speaking on THAT reference is what
; gives the line a real voice folder.
;
; abInHead is TES4's fourth argument and Skyrim's own third one, native on
; Say: the voice comes from inside the player's head, at full volume,
; wherever the player stands -- as in Oblivion, where the Arena announcer,
; the Daedric princes and Mankar Camoran were heard regardless of the
; marker's position.

Function SpeakAs(ObjectReference akSpeaker, Bool abInHead = False, Topic akTopic = None) Global
  ; TES4 `marker.Say <topic> 1 <speak-as NPC> <in-head>` -- a line spoken
  ; THROUGH a marker/shrine/door AS some NPC.  The importer gives each such
  ; call site a talking activator carrying that NPC's voice type
  ; (tes5_import/speaker_activators.py); speaking on THAT reference is what
  ; gives the line a real voice folder, which a bare XMarker STAT has not.
  ;
  ; 🛑 THIS IS A PLAIN Say().  Two cleverer deliveries were tried and both
  ; KILLED THE AUDIO outright (worse than the defect they targeted):
  ;   * a one-action SCEN per call site;
  ;   * Activate() on the talking activator (vanilla's own idiom -- but
  ;     vanilla activates a TACT the PLAYER walked up to, which is not what a
  ;     polled announcer line is).
  ; Say() on the voiced stand-in is the only form measured to produce audio.
  ;
  ; abInHead is TES4's fourth argument and Skyrim's own third one, native on
  ; Say: the voice comes from inside the player's head, at full volume,
  ; wherever they stand.  🛑 NEVER emulate it by MoveTo'ing the speaker onto
  ; the player -- that teleports the marker out of its authored position
  ; permanently and costs the line its audio.
  If akSpeaker == None || akTopic == None
    Return
  EndIf
  akSpeaker.Say(akTopic, None, abInHead)
EndFunction

; The measuring form: `set T to marker.Say topic 1 voice 1` -- returns the
; selected line's real length, exactly as SayLine does for an actor.  The
; INFO's Begin fragment reports it through LineBegan, which for a non-actor
; speaker stashes it on the PLAYER (Variable09; the game-wide "a line is
; playing until" record in Variable07 is stamped as for any speaker).
Float Function SpeakAsLine(ObjectReference akSpeaker, Float afFallbackLength, Bool abInHead = False, Topic akTopic = None) Global
  ; The measuring form: TES4 `set T to marker.Say topic 1 voice 1` returned
  ; the selected line's length and the caller counted it down.  Delivery is
  ; the plain Say above (the only form measured to produce audio); the length
  ; comes from the INFO's Begin fragment, which stashes it on the PLAYER for a
  ; non-actor speaker (see LineBegan).
  If akSpeaker == None || akTopic == None
    Return afFallbackLength
  EndIf
  Actor gp = Game.GetPlayer()
  gp.SetActorValue("Variable09", 0.0)
  SpeakAs(akSpeaker, abInHead, akTopic)
  Float t = 0.0
  While t < SAY_START_WAIT() && gp.GetActorValue("Variable09") == 0.0
    Utility.Wait(0.05)
    t += 0.05
  EndWhile
  Float len = gp.GetActorValue("Variable09")
  If len <= 0.0
    Return afFallbackLength      ; no length reported: fall back, never 0
  EndIf
  If len < 0.02
    Return afFallbackLength      ; began, but the line has no measured voice file
  EndIf
  Return len
EndFunction

; The non-blocking form, for the engine's dispatch path (see SayLineNoWait).
Float Function SpeakAsLineNoWait(ObjectReference akSpeaker, Float afFallbackLength, Bool abInHead = False, Topic akTopic = None) Global
  SpeakAs(akSpeaker, abInHead, akTopic)
  Return afFallbackLength
EndFunction


; ======================================================== oblivion gates ==
;
; TES4's CloseCurrentOblivionGate / CloseOblivionGate / ForceCloseOblivionGate
; are ENGINE calls with no Papyrus counterpart.  CloseCurrentOblivionGate does
; three things at once (UESP, "Oblivion:Oblivion Gates", console section:
; "This will close the gate you entered through and teleport you back to
; Cyrodiil"):
;
;   1. teleport the player out of the Oblivion worldspace, back to the gate;
;   2. mark that gate destroyed, so its ACTI stops spawning and the sigil
;      chain's `getdestroyed == 1` branches fire;
;   3. drop the gate's forced weather override.
;
; Converting it to a no-op severed the ONLY way out of every Oblivion realm:
; the player took the Sigil Stone, got the item, the fame and the fireworks,
; and then stood there forever.  Both of Bethesda's redundant exit routes end
; in this same call -- SigilRingBoomSCRIPT's 8.666s `gateTimer`, and the
; eleven TrigZoneCloseCurrentOblivion* trigger zones gated on `gotSigil == 1`
; -- so a single no-op killed all of them at once.  (Oblivion itself shipped
; this as a known bug on the Bruma gate, whose UESP workaround is to type
; CloseCurrentOblivionGate in the console: that function IS the exit.)
;
; WHICH GATE.  Skyrim has no "the gate you came through" concept, so the
; return target has to be captured on the way IN.  MQ00Script.nearOblivionGate
; holds exactly that reference while the player is beside the gate in Tamriel
; -- but every gate script CLEARS it to 0 in its own OnActivate ("we aren't
; 'near' any gate anymore -- we're in Oblivion!"), which is the same event
; that carries the player through.  So the converter injects a capture call
; BEFORE that clear (see _is_oblivion_gate_entry): the gate records ITSELF
; here as the player entered, and the clear then proceeds untouched so the
; authored weather/proximity logic is unchanged.
;
; WHERE IT IS STORED.  As the FormID of the gate, split hi/lo across two
; script Actor Values ON THE PLAYER -- the same mechanism LineBegan uses to
; remember the last actor to speak in the player's menu, and for the same
; reasons: it is vanilla-only, it persists across save/load and worldspace
; changes, and it needs no property binding on either side.
;
;   Variable01  hi half of the entered gate's FormID (0 = no gate held)
;   Variable02  lo half
;
; Variable01/02 are the only two of the engine's ten script AVs this
; conversion does not already use (03-10 are the say-line state above).
;
; A script variable on the GATE could not do this job: the gate's own cell is
; unloaded the entire time the player is inside the realm.  Nor could a
; linked ref -- Papyrus has GetLinkedRef but NO SetLinkedRef (it exists only
; in po3's SKSE plugin), so linked refs are editor-authored and read-only at
; runtime.

; Record `akGate` as the gate the player is currently entering.
Function EnterOblivionGate(ObjectReference akGate) Global
  If akGate == None
    Return
  EndIf
  Int fid = (akGate as Form).GetFormID()
  Actor p = Game.GetPlayer()
  p.SetActorValue("Variable01", Math.Floor(fid / 65536) as Float)
  p.SetActorValue("Variable02", (fid - Math.Floor(fid / 65536) * 65536) as Float)
EndFunction

; The gate the player entered through, or None if none was captured.
ObjectReference Function GetCurrentOblivionGate() Global
  Actor p = Game.GetPlayer()
  Float hi = p.GetActorValue("Variable01")
  Float lo = p.GetActorValue("Variable02")
  If hi <= 0.0 && lo <= 0.0
    Return None
  EndIf
  ; A runtime-created (FF……) reference cannot be rebuilt from an Int — the
  ; same guard PlayerIsInDialogue uses.  No vanilla or converted gate is
  ; runtime-created, but a PlaceAtMe'd one would land here.
  If hi >= 32768.0
    ClearOblivionGate()
    Return None
  EndIf
  Return Game.GetForm((hi as Int) * 65536 + (lo as Int)) as ObjectReference
EndFunction

Function ClearOblivionGate() Global
  Actor p = Game.GetPlayer()
  p.SetActorValue("Variable01", 0.0)
  p.SetActorValue("Variable02", 0.0)
EndFunction

; TES4 GetDisabled.
;
; In Oblivion "disabled" and "destroyed" are INDEPENDENT bits of the same
; reference (0x800 and 0x2000 of [ref+8]), and closing a gate set only the
; destroyed one -- so a closed gate stayed enabled and `getdisabled` kept
; reporting 0.  Scripts rely on that: MS48 and MS94 both open their poll with
;   if getdisabled == 1
;       return
;   endif
;   if getdestroyed == 1 && getstage <q> < N
;       setstage <q> N
; where the preamble is meant to skip a gate that was never OPENED, not one
; that has just been CLOSED.
;
; Skyrim has no separate "present" bit we can clear, so removing a closed gate
; has to use Disable() (see TurnGateOff).  That makes the native IsDisabled()
; true and turns those preambles into a blocker, permanently stranding the
; setstage below them -- the measured MS48-at-stage-10 defect.
;
; Restoring Oblivion's independence is one line: a DESTROYED reference is not
; reported as disabled.  The preamble keeps its original meaning (skip an
; unopened gate) and the destroyed branch stays reachable.
Bool Function GetDisabled(ObjectReference akRef, FormList akDestroyed) Global
  If akRef == None
    Return False
  EndIf
  If GetDestroyed(akRef, akDestroyed)
    Return False
  EndIf
  Return akRef.IsDisabled()
EndFunction

; Switch a gate off so it stops being drawn (and stops emitting its DOOR
; BNAM loop sound).
;
; SetDestroyed alone does NOT do this -- per the CK wiki a destroyed object
; "still exists, and continues to render and process events normally".  It is
; only non-interactable.  Taking it out of the world is Disable().
;
; A GATE CANNOT ALWAYS Disable() ITSELF.  Skyrim refuses Disable() on a
; reference that has an enable-state parent, logging
;   "(1201E8A3): cannot disable an object with an enable state parent."
; and doing nothing -- the live Papyrus error from the Kvatch gate, whose
; REFR carries XESP.Reference=00091229.  The parent IS the authored on/off
; switch: OblivionGateRandomScript reopens a spent gate with
; `set mySpawnMarker to getParentRef / mySpawnMarker.enable`.
;
; So: try the direct Disable() first and ASK THE ENGINE whether it took
; (IsDisabled()), rather than predicting which case applies.  If it was
; refused, walk up the enable-parent chain instead -- it can nest
; (OblivionRD001Gate01 -> 03 -> 02), hence the loop and the guard.
; The importer mirrors XESP into XLKR for gate-closing bases so GetLinkedRef
; can reach the parent at runtime (object_scripts._GETPARENTREF_BASES).
Function TurnGateOff(ObjectReference akGate) Global
  If akGate == None
    Return
  EndIf
  akGate.Disable()
  If akGate.IsDisabled()
    Return
  EndIf
  ; Refused => it has an enable parent.  Switch that off instead, walking up
  ; in case the chain nests.
  ObjectReference enabler = akGate.GetLinkedRef()
  Int guard = 0
  While enabler != None && guard < 8
    enabler.Disable()
    If enabler.IsDisabled()
      Return
    EndIf
    enabler = enabler.GetLinkedRef()
    guard += 1
  EndWhile
EndFunction

; CloseCurrentOblivionGate: teleport the player back to the gate they entered
; through, set its destroyed flag, and release the gate weather.
;
; That flag IS the whole closure -- verified against Oblivion.exe.  The
; command handler (0x515ef0) reaches ONLY the flag setter (0x46aa50, which
; ORs 0x2000 into [ref+8]); Disable (0x50a240, bit 0x800) is NOT reachable
; from it at any depth.  The visible portal is the looping `SpecialIdle` the
; gate's own GameMode re-issues while `GetDestroyed == 0`; once the flag is
; set the poll stops re-issuing it and the portal closes on its own.  So the
; conversion must NOT Disable() the gate -- Oblivion never does, and doing so
; would also make the poll's `if getdisabled == 1 / return` preamble swallow
; the `setstage` that the same poll owes the quest.
;
; The gate is ALWAYS destroyed.  TES4's optional integer argument is a "no
; reset" flag about the Oblivion CELL respawning, not about the gate: the two
; callers that pass 1 are named for it (SigilRingBoomNoResetSCRIPT and the
; TrigZone*NoResetSCRIPTs) and are otherwise byte-identical to the variants
; that pass nothing.  Treating it as "don't destroy" was measured wrong in
; game -- the Kvatch gate stayed standing and MS48 pinned at stage 10, since
; MS48OblivionGateScript's only `setstage ms48 50` is gated on
; `getdestroyed == 1`.
;
; Returns False when no gate was captured -- the player reached a realm by
; some route that never ran a gate OnActivate (a coc, a scripted MoveTo).
; Nothing sensible can be done in that case, and teleporting to a stale
; reference would be worse than staying put.
Bool Function CloseCurrentOblivionGate(FormList akDestroyed) Global
  ObjectReference gate = GetCurrentOblivionGate()
  If gate == None
    Return False
  EndIf
  ; Clear the capture FIRST: whatever happens below, this gate is spent, and a
  ; stale hold would send a later realm's exit to the wrong worldspace.
  ClearOblivionGate()
  ; Out of the realm first, matching the engine: the gate is a reference in
  ; the DESTINATION cell, so flagging it before the move would touch an
  ; object the player is still transitioning toward.
  Game.GetPlayer().MoveTo(gate)
  ; Order matters, and no timer is needed.  The gate's own GameMode poll
  ; opens with `if getdisabled == 1 / return`, ABOVE its `getdestroyed`
  ; stage check -- so switching the gate off is what once made the quest
  ; unreachable.  It no longer can: `getdestroyed` now reads the FormList,
  ; which SetDestroyed writes on THIS line, before the gate goes away.  The
  ; stage branch is satisfied by state, not by catching a live poll tick.
  SetDestroyed(gate, akDestroyed)
  TurnGateOff(gate)
  ; The realm's weather override does not survive the player leaving it, and
  ; the gate's own GameMode poll re-applies OblivionStormTamriel while the
  ; player stands next to it.
  Weather.ReleaseOverride()
  Return True
EndFunction

; TES4 GetDestroyed / SetDestroyed -- the destroyed FLAG.
;
; CK wiki, SetDestroyed: "Objects that have been Destroyed no longer present
; mouseover text and cannot be activated.  Note that they still exist, and
; continue to render and process events normally - they are not Disabled or
; Deleted, and their visual Destruction State, if any, is unaffected."
;
; So it is ONLY non-interactability -- three separate states share the word
; "destroyed" and must not be conflated: the FLAG (bit 0x2000 of [ref+8] in
; Oblivion.exe), enable state (bit 0x800, what Disable() writes), and the DEST
; destruction STAGE.  GetCurrentDestructionStage() and IsDisabled() each read
; one of the other two, and using either made `if getdestroyed == 1 / setstage`
; unreachable -- that is what pinned MS48 at stage 10.
;
; Skyrim exposes the setter to Papyrus (ObjectReference.SetDestroyed) but NOT
; the getter: GetDestroyed is a console/condition function (CTDA index 203),
; with no ObjectReference member -- 0 hits across every vanilla .psc.  So the
; flag is mirrored into a conversion-owned FormList as it is written.
; A FormList works on ANY reference type (the AV store used elsewhere here is
; Actor-only, and a gate is a DOOR) and its added entries persist in the save.
Function SetDestroyed(ObjectReference akRef, FormList akDestroyed, Bool abDestroyed = True) Global
  If akRef == None
    Return
  EndIf
  akRef.SetDestroyed(abDestroyed)
  If akDestroyed == None
    Return
  EndIf
  ; HasForm first: AddForm would otherwise stack duplicate entries on a
  ; reference destroyed twice, and RemoveAddedForm only drops one of them.
  If abDestroyed
    If !akDestroyed.HasForm(akRef)
      akDestroyed.AddForm(akRef)
    EndIf
  Else
    While akDestroyed.HasForm(akRef)
      akDestroyed.RemoveAddedForm(akRef)
    EndWhile
  EndIf
EndFunction

Bool Function GetDestroyed(ObjectReference akRef, FormList akDestroyed) Global
  If akRef == None || akDestroyed == None
    Return False
  EndIf
  Return akDestroyed.HasForm(akRef)
EndFunction

; ForceCloseOblivionGate / CloseOblivionGate: destroy a gate WITHOUT moving the
; player.  Called on the Tamriel side -- OblivionGateRandomScript and
; MS94/MQ11OblivionGateScript each call it from OnLoad to clean up gates still
; standing after MQ16 ends.  `akGate` is the calling reference itself.
Function CloseOblivionGate(ObjectReference akGate, FormList akDestroyed) Global
  If akGate == None
    Return
  EndIf
  SetDestroyed(akGate, akDestroyed)
  TurnGateOff(akGate)
EndFunction
