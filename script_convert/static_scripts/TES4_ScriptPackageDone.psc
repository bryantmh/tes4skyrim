ScriptName TES4_ScriptPackageDone extends Package Hidden
{Shared OnEnd fragment of a package Oblivion forced on with AddScriptPackage.
Such a package runs from the call until it completes; the actor is in the
package's faction (Armed) for exactly that long, and the package's first
condition tests it. Completing takes the actor out and re-checks its packages.
See docs/commentary/tes5_import_package.md#package-hosts}

Faction Property Armed Auto

Function Fragment_0(Actor akActor)
  akActor.RemoveFromFaction(Armed)
  TES4Polyfill.ReevaluateAfterEnd(akActor, Self)
EndFunction
