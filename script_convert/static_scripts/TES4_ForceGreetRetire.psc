ScriptName TES4_ForceGreetRetire extends Package Hidden
{OnEnd fragment of a MONOTONIC (>=/>) GetStage-gated force-greet package: once
the forced greeting has run, set the per-greet latch so the package's own
GetGlobalValue(Latch) == 0 guard falsifies and the greet never re-fires.

Oblivion re-evaluated AI continuously, so a `>= stage` gate that never re-closes
would force the greeting again on every dialogue-exit re-eval -- the
CGBaurusGreetPlayer loop.  The latch is a persistent GLOB, so the retire holds
across saves.  Mirrors the retire pattern of Path B's TES4_ForceGreetDone.}

GlobalVariable Property Latch Auto

Function Fragment_0(Actor akActor)
  If Latch
    Latch.SetValue(1.0)
  EndIf
EndFunction
