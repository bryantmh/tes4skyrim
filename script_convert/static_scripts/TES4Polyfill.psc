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
; Day/Time Helpers
; ==========================================================================

Int Function GetDayOfWeek() Global
  Return ((GameDaysPassed.GetValue() as Int) % 7)
EndFunction

Float Function GetCurrentTime() Global
  Return Utility.GetCurrentGameTime()
EndFunction

GlobalVariable Property GameDaysPassed Auto
