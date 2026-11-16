ScriptName TES4_StagePackageAlias extends ReferenceAlias Hidden
{A converted alias carrying an actor's AI packages. Oblivion re-checked an
actor's packages on its own once a stage they were gated on was set; Skyrim
only does on EvaluatePackage. TES4Polyfill.StageSet sends the quest's event
after every converted SetStage, and each alias re-checks its own actor on its
own thread, so the caller never waits. On the package host, Source1..8 are the
quests whose stages gate this actor's packages; an actor gated on more sets
Every and hears every stage set. With none set it is the owning quest.
See docs/commentary/script_convert.md#setstage-re-evaluates-alias-packages}

Quest Property Source1 Auto
Quest Property Source2 Auto
Quest Property Source3 Auto
Quest Property Source4 Auto
Quest Property Source5 Auto
Quest Property Source6 Auto
Quest Property Source7 Auto
Quest Property Source8 Auto
Quest Property Every Auto

Event OnInit()
  If Every
    RegisterForModEvent(TES4Polyfill.AnyStageEventName(), "OnTES4StageSet")
  ElseIf Source1 == None
    Listen(GetOwningQuest())
  Else
    Listen(Source1)
    Listen(Source2)
    Listen(Source3)
    Listen(Source4)
    Listen(Source5)
    Listen(Source6)
    Listen(Source7)
    Listen(Source8)
  EndIf
EndEvent

Function Listen(Quest akQuest)
  If akQuest
    RegisterForModEvent(TES4Polyfill.StageEventName(akQuest), "OnTES4StageSet")
  EndIf
EndFunction

Event OnTES4StageSet(String asEventName, String asArg, Float afArg, Form akSender)
  Actor who = GetActorReference()
  If who
    who.EvaluatePackage()
  EndIf
EndEvent
