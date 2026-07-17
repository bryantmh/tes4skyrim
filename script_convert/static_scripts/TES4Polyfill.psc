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

; ==========================================================================
; Actor Value Mapping (TES4 AV names → TES5 AV names)
; ==========================================================================

String Function MapActorValue(String avName) Global
  ; Attributes (removed in TES5 — map to closest equivalent)
  If avName == "Strength"
    Return "UnarmedDamage"
  ElseIf avName == "Intelligence"
    Return "Magicka"
  ElseIf avName == "Willpower"
    Return "MagickaRate"
  ElseIf avName == "Agility"
    Return "SpeedMult"
  ElseIf avName == "Speed"
    Return "SpeedMult"
  ElseIf avName == "Endurance"
    Return "HealRate"
  ElseIf avName == "Personality"
    Return "Speechcraft"
  ElseIf avName == "Luck"
    Return "Health"
  ; Skills (renamed in TES5)
  ElseIf avName == "Armorer"
    Return "Smithing"
  ElseIf avName == "Athletics"
    Return "Stamina"
  ElseIf avName == "Blade"
    Return "OneHanded"
  ElseIf avName == "Blunt"
    Return "TwoHanded"
  ElseIf avName == "HandToHand"
    Return "UnarmedDamage"
  ElseIf avName == "Mysticism"
    Return "Alteration"
  ElseIf avName == "Mercantile"
    Return "Speechcraft"
  ElseIf avName == "Security"
    Return "Lockpicking"
  ElseIf avName == "Acrobatics"
    Return "SpeedMult"
  ElseIf avName == "Fatigue"
    Return "Stamina"
  ElseIf avName == "Encumbrance"
    Return "CarryWeight"
  Else
    Return avName
  EndIf
EndFunction

Float Function GetTES4ActorValue(Actor akActor, String avName) Global
  Return akActor.GetActorValue(MapActorValue(avName))
EndFunction

Function SetTES4ActorValue(Actor akActor, String avName, Float afValue) Global
  akActor.SetActorValue(MapActorValue(avName), afValue)
EndFunction

Function ModTES4ActorValue(Actor akActor, String avName, Float afValue) Global
  akActor.ModActorValue(MapActorValue(avName), afValue)
EndFunction

Function ForceTES4ActorValue(Actor akActor, String avName, Float afValue) Global
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
