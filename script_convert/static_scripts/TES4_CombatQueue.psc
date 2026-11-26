ScriptName TES4_CombatQueue extends Quest Hidden
{The TES4CombatApproaches pool's own thread. A converted StartCombat or
StopCombat queues here and returns at once, as TES4's do, so a script that
starts a dozen fights keeps its own timing; OnUpdate carries the calls out in
the order they were made, one at a time, so two fights never race for a pair.
It remembers which attacker and target hold each pair, so a pair is found
without asking the aliases.
See docs/commentary/script_convert.md#startcombat-approaches-an-undetected-target}

; Attacker/target alias pairs the importer writes (combat_approach.PAIRS).
Int Property Pairs = 32 AutoReadOnly

; Queued calls, a ring of 128 (Papyrus's array limit); a None target is a StopCombat.
Actor[] Attackers
Actor[] Targets
Faction[] AttackerFactions
Faction[] VictimFactions
Int Head = 0
Int Count = 0
Bool Draining = False

; Attacker (alias 2n) and target (alias 2n+1) of pair n, and the real time it was last started.
Actor[] HeldAttackers
Actor[] HeldTargets
Float[] HeldAt

; Queue a StartCombat (akTarget set) or a StopCombat (akTarget None); False when the queue is full.
Bool Function Push(Actor akAttacker, Actor akTarget, Faction akAttackers, Faction akVictims)
  If !Attackers
    Attackers = new Actor[128]
    Targets = new Actor[128]
    AttackerFactions = new Faction[128]
    VictimFactions = new Faction[128]
  EndIf
  Int last = LastQueued(akAttacker)
  If last >= 0 && Targets[last] == akTarget
    Return True
  EndIf
  If Count >= 128
    Return False
  EndIf
  Int slot = (Head + Count) % 128
  Attackers[slot] = akAttacker
  Targets[slot] = akTarget
  AttackerFactions[slot] = akAttackers
  VictimFactions[slot] = akVictims
  Count += 1
  If !IsRunning()
    Start()
  EndIf
  RegisterForSingleUpdate(0.01)
  Return True
EndFunction

; Ring slot of `akAttacker`'s latest queued call, -1 when none; a repeat of it is not queued again.
Int Function LastQueued(Actor akAttacker)
  Int i = Count - 1
  While i >= 0
    Int slot = (Head + i) % 128
    If Attackers[slot] == akAttacker
      Return slot
    EndIf
    i -= 1
  EndWhile
  Return -1
EndFunction

Event OnUpdate()
  If Draining
    Return
  EndIf
  Draining = True
  While Count > 0
    Actor attacker = Attackers[Head]
    Actor target = Targets[Head]
    Faction attackerFaction = AttackerFactions[Head]
    Faction victimFaction = VictimFactions[Head]
    Attackers[Head] = None
    Targets[Head] = None
    Head = (Head + 1) % 128
    Count -= 1
    If target
      Bool adds = AddsTarget(attacker, target)
      If TES4Polyfill.ForceCombatNow(attacker, target, attackerFaction, victimFaction, adds)
        If adds
          Release(attacker)
          attacker.EvaluatePackage()
        Else
          Hold(attacker, target)
        EndIf
      EndIf
    Else
      Actor foe = attacker.GetCombatTarget()
      Release(attacker)
      TES4Polyfill.EndCombat(attacker, attackerFaction)
      NoteStop(attacker, foe)
    EndIf
  EndWhile
  Draining = False
  If RetryHeld()
    RegisterForSingleUpdate(3.0)
  EndIf
EndEvent

; Actors stopped in the last second and the foe each was fighting.
Actor[] StoppedActors
Actor[] StoppedFoes
Float[] StoppedAt

; Note `akActor`'s stop. When an actor it fought each other with was stopped within a second, the
; two stop together as TES4's one-frame stops did: every fight against either ends, the one a
; last swing restarts included. See docs/commentary/script_convert.md#se02-gatekeeper-killed-by-his-ally
Function NoteStop(Actor akActor, Actor akFoe)
  If !StoppedActors
    StoppedActors = new Actor[8]
    StoppedFoes = new Actor[8]
    StoppedAt = new Float[8]
  EndIf
  Float now = Utility.GetCurrentRealTime()
  Actor player = Game.GetPlayer()
  Int i = 0
  While i < 8
    Actor other = StoppedActors[i]
    If other && other != akActor && akActor != player && other != player && now - StoppedAt[i] < 1.0 && (StoppedFoes[i] == akActor || akFoe == other)
      akActor.StopCombatAlarm()
      other.StopCombatAlarm()
    EndIf
    i += 1
  EndWhile
  Int slot = OldestStop()
  StoppedActors[slot] = akActor
  StoppedFoes[slot] = akFoe
  StoppedAt[slot] = now
EndFunction

Int Function OldestStop()
  Int oldest = 0
  Int i = 1
  While i < 8
    If StoppedAt[i] < StoppedAt[oldest]
      oldest = i
    EndIf
    i += 1
  EndWhile
  Return oldest
EndFunction

; TES4's melee alert (Oblivion.exe 0x622d40): an attacker whose path failed stands ready and
; re-plans every 3 s, where Skyrim's ends the fight. A held attacker out of combat, with it and
; its target alive and loaded, starts again; True while such a pair is held, so the retry goes on.
; See docs/commentary/script_convert.md#startcombat-retries-like-melee-alert
Bool Function RetryHeld()
  LoadPairs()
  Bool live = False
  Int n = 0
  While n < Pairs
    Actor attacker = HeldAttackers[n]
    Actor target = HeldTargets[n]
    If attacker && target && !attacker.IsDead() && !target.IsDead() && attacker.Is3DLoaded() && target.Is3DLoaded()
      live = True
      If !attacker.IsInCombat()
        TES4Polyfill.ForceCombatNow(attacker, target, None, None)
      EndIf
    EndIf
    n += 1
  EndWhile
  Return live
EndFunction

; True when this StartCombat follows, within a second, one this queue made on another live target:
; TES4 calls in one frame ADD targets (Oblivion.exe 0x514660), so it joins that fight rather than
; standing the attacker down. A pair aims at one target, so a fight with several is left to
; Skyrim's own combat. See docs/commentary/script_convert.md#startcombat-approaches-an-undetected-target
Bool Function AddsTarget(Actor akAttacker, Actor akTarget)
  LoadPairs()
  Int n = HeldAttackers.Find(akAttacker)
  If n < 0 || HeldTargets[n] == akTarget || !HeldTargets[n] || HeldTargets[n].IsDead()
    Return False
  EndIf
  Return Utility.GetCurrentRealTime() - HeldAt[n] < 1.0
EndFunction

; Put the pair in the attacker's pair, else an empty one or one whose actors are dead, else pair 0.
Function Hold(Actor akAttacker, Actor akTarget)
  Int n = PairOf(akAttacker)
  HeldAt[n] = Utility.GetCurrentRealTime()
  If HeldAttackers[n] == akAttacker && HeldTargets[n] == akTarget
    Return
  EndIf
  HeldAttackers[n] = akAttacker
  HeldTargets[n] = akTarget
  (GetAlias(2 * n + 1) as ReferenceAlias).ForceRefTo(akTarget)
  (GetAlias(2 * n) as ReferenceAlias).ForceRefTo(akAttacker)
  akAttacker.EvaluatePackage()
EndFunction

; Empty the pair `akActor` attacks from, if any.
Function Release(Actor akActor)
  If !akActor
    Return
  EndIf
  LoadPairs()
  Int n = HeldAttackers.Find(akActor)
  If n < 0
    Return
  EndIf
  HeldAttackers[n] = None
  HeldTargets[n] = None
  (GetAlias(2 * n + 1) as ReferenceAlias).Clear()
  (GetAlias(2 * n) as ReferenceAlias).Clear()
EndFunction

Int Function PairOf(Actor akAttacker)
  LoadPairs()
  Int n = HeldAttackers.Find(akAttacker)
  If n < 0
    n = HeldAttackers.Find(None)
  EndIf
  Int i = 0
  While n < 0 && i < Pairs
    If HeldAttackers[i].IsDead() || !HeldTargets[i] || HeldTargets[i].IsDead()
      n = i
    EndIf
    i += 1
  EndWhile
  If n < 0
    n = 0
  EndIf
  Return n
EndFunction

; Read the aliases once per save, for pairs filled before this script kept track.
Function LoadPairs()
  If !HeldAt
    HeldAt = new Float[32]
  EndIf
  If HeldAttackers
    Return
  EndIf
  Actor[] attackers = new Actor[32]
  Actor[] targets = new Actor[32]
  Int n = 0
  While n < Pairs
    attackers[n] = (GetAlias(2 * n) as ReferenceAlias).GetActorReference()
    targets[n] = (GetAlias(2 * n + 1) as ReferenceAlias).GetActorReference()
    n += 1
  EndWhile
  HeldTargets = targets
  HeldAttackers = attackers
EndFunction
