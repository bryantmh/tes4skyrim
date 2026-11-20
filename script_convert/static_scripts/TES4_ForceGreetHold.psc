ScriptName TES4_ForceGreetHold extends Package Hidden
{OnBegin fragment of a converted force greet with no other fragment: the
conversation it opens cannot be left while one of its lines offers choices,
as Oblivion hides Goodbye then (TES4Polyfill.HoldExitUntilAnswered).
See docs/commentary/tes_runtime_dialogue.md#no-exit-while-choices-are-offered}

Function Fragment_0(Actor akActor)
  TES4Polyfill.HoldExitUntilAnswered(akActor)
EndFunction
