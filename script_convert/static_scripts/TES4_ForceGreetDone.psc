ScriptName TES4_ForceGreetDone extends Package Hidden
{Shared OnEnd fragment of the TES4ForceGreets packages: once the forced
greeting has run, empty the alias (Slot) that handed the actor this package,
so it returns to its own schedule. The retire pattern of vanilla's
PF_WITavernServerGreetPlayer. Its OnBegin fragment holds the conversation's
exit until a line's choices are answered (TES4Polyfill.HoldExitUntilAnswered).}

Int Property Slot Auto

Function Fragment_0(Actor akActor)
  ReferenceAlias owner = GetOwningQuest().GetAlias(Slot) as ReferenceAlias
  If owner && owner.GetReference() == akActor
    owner.Clear()
  EndIf
EndFunction

Function Fragment_Hold(Actor akActor)
  TES4Polyfill.HoldExitUntilAnswered(akActor)
EndFunction
