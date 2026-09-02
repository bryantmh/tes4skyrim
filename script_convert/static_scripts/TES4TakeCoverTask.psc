Scriptname TES4TakeCoverTask extends ObjectReference
{One asynchronous TES4 ForceTakeCover invocation.}

Faction Property TES4ForceCombatAttackers Auto
Faction Property TES4ForceCombatVictims Auto

Actor Subject
Actor Threat
Actor PreviousTarget
Float PreviousConfidence
Int SubjectFactionRank
Int ThreatFactionRank
Bool WasInCombat

Function BeginTask(Actor akSubject, Actor akThreat, Float afDuration)
  Subject = akSubject
  Threat = akThreat
  If Subject == None || Threat == None
    FinishTask()
    Return
  EndIf

  PreviousConfidence = Subject.GetActorValue("Confidence")
  PreviousTarget = Subject.GetCombatTarget()
  WasInCombat = Subject.IsInCombat()
  If TES4ForceCombatAttackers != None && TES4ForceCombatVictims != None
    SubjectFactionRank = Subject.GetFactionRank(TES4ForceCombatAttackers)
    ThreatFactionRank = Threat.GetFactionRank(TES4ForceCombatVictims)
    If SubjectFactionRank < 0
      Subject.AddToFaction(TES4ForceCombatAttackers)
    EndIf
    If ThreatFactionRank < 0
      Threat.AddToFaction(TES4ForceCombatVictims)
    EndIf
  Else
    SubjectFactionRank = -1
    ThreatFactionRank = -1
  EndIf

  Subject.SetActorValue("Confidence", 0.0)
  Subject.StartCombat(Threat)
  Subject.EvaluatePackage()
  RegisterForSingleUpdate(afDuration)
EndFunction

Event OnUpdate()
  FinishTask()
EndEvent

Function FinishTask()
  If Subject != None
    Subject.SetActorValue("Confidence", PreviousConfidence)
    If !WasInCombat
      Subject.StopCombat()
    ElseIf PreviousTarget != None && PreviousTarget != Threat
      Subject.StartCombat(PreviousTarget)
    EndIf
    If SubjectFactionRank < 0 && TES4ForceCombatAttackers != None
      Subject.RemoveFromFaction(TES4ForceCombatAttackers)
    EndIf
    Subject.EvaluatePackage()
  EndIf
  If Threat != None && ThreatFactionRank < 0 && TES4ForceCombatVictims != None
    Threat.RemoveFromFaction(TES4ForceCombatVictims)
  EndIf
  Disable()
  Delete()
EndFunction
