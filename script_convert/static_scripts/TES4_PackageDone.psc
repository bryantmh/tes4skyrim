ScriptName TES4_PackageDone extends Package Hidden
{Shared OnEnd fragment of a quest-gated Oblivion "Once Per Day" package: when
the package completes, its latch global (Latch) is set to the next whole game
day, so the package's first condition (GameDaysPassed >= Latch) fails until
then and the actor's next package gets its turn, as in Oblivion.}

GlobalVariable Property Latch Auto

Function Fragment_0(Actor akActor)
  Latch.SetValue(Math.Floor(Utility.GetCurrentGameTime()) + 1)
  TES4Polyfill.ReevaluateAfterEnd(akActor, Self)
EndFunction
