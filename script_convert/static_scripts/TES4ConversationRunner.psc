Scriptname TES4ConversationRunner extends Quest
{Runs source TCLT graphs asynchronously; INFO End fragments choose the route.}

Actor[] _queueA
Actor[] _queueB
Topic[] _queueTopic
Int _read = 0
Int _write = 0
Actor _speaker
Actor _listener
Topic _topic
Int _choice = 0
Int _lastInfo = 0
Int _nextSpeaker = 1
Int _endedInfo = 0
Int _startedInfo = 0
Bool _waiting = False
Bool _began = False
Bool _eitherTried = False
Bool _updating = False
Float _deadline = 0.0
Int _steps = 0

Function EnsureArrays()
  If _queueA == None
    _queueA = new Actor[32]
    _queueB = new Actor[32]
    _queueTopic = new Topic[32]
  EndIf
EndFunction

Bool Function Enqueue(Actor akSpeaker, Actor akListener, Topic akTopic)
  EnsureArrays()
  If akSpeaker == None || akListener == None || akTopic == None
    Return False
  EndIf
  Int next = (_write + 1) % 32
  If next == _read
    Debug.Trace("TES4 conversation queue is full")
    Return False
  EndIf
  _queueA[_write] = akSpeaker
  _queueB[_write] = akListener
  _queueTopic[_write] = akTopic
  _write = next
  ; Select the first line before the caller's next statement changes stages.
  ; This path uses SayLineNoWait: never block an engine callback or fragment.
  BeginPending()
  TryTopic()
  RegisterForSingleUpdate(0.1)
  Return True
EndFunction

TES4ConversationRunner Function Owner(Actor akActor) Global
  If akActor == None || akActor == Game.GetPlayer()
    Return None
  EndIf
  Int fid = (akActor.GetActorValue("Variable01") as Int) * 65536
  fid += akActor.GetActorValue("Variable02") as Int
  If fid == 0
    Return None
  EndIf
  Return Game.GetForm(fid) as TES4ConversationRunner
EndFunction

Function Mark(Actor akActor, Int aiID)
  If akActor != None && akActor != Game.GetPlayer()
    akActor.SetActorValue("Variable01", (aiID / 65536) as Float)
    akActor.SetActorValue("Variable02", (aiID % 65536) as Float)
  EndIf
EndFunction

Function NotifyBegin(ObjectReference akSpeaker, Int aiInfo) Global
  TES4ConversationRunner runner = Owner(akSpeaker as Actor)
  If runner != None
    runner.Began(akSpeaker as Actor, aiInfo)
  EndIf
EndFunction

Function NotifyEnd(ObjectReference akSpeaker, Int aiInfo) Global
  TES4ConversationRunner runner = Owner(akSpeaker as Actor)
  If runner != None
    runner.Ended(akSpeaker as Actor, aiInfo)
  EndIf
EndFunction

Function Began(Actor akSpeaker, Int aiInfo)
  If _waiting && akSpeaker == _speaker && Matches(aiInfo, _topic)
    _began = True
    _startedInfo = aiInfo
    _deadline = Utility.GetCurrentRealTime() + 120.0
  EndIf
EndFunction

Bool Function Matches(Int aiInfo, Topic akTopic)
  Return False
EndFunction

Function Ended(Actor akSpeaker, Int aiInfo)
  If _waiting && akSpeaker == _speaker && _began && aiInfo == _startedInfo
    _endedInfo = aiInfo
    RegisterForSingleUpdate(0.1)
  EndIf
EndFunction

Topic Function NextChoice(Int aiInfo, Int aiCandidate)
  Return None
EndFunction

; Overridden by the generated plugin graph. It fills choices in source order
; and returns DATA.NextSpeaker (0 Target, 1 Self, 2 Either).
Int Function Route(Int aiInfo)
  Return 1
EndFunction

Function Finish()
  If Owner(_speaker) == Self
    Mark(_speaker, 0)
  EndIf
  If Owner(_listener) == Self
    Mark(_listener, 0)
  EndIf
  _speaker = None
  _listener = None
  _topic = None
  _waiting = False
  _began = False
  _endedInfo = 0
  _startedInfo = 0
EndFunction

Function AdvanceCandidate()
  If _nextSpeaker == 2 && !_eitherTried && _speaker != _listener
    Actor temp = _speaker
    _speaker = _listener
    _listener = temp
    _eitherTried = True
    Return
  EndIf
  If _nextSpeaker == 2 && _eitherTried
    Actor temp2 = _speaker
    _speaker = _listener
    _listener = temp2
  EndIf
  _eitherTried = False
  _choice += 1
  _topic = NextChoice(_lastInfo, _choice)
  If _topic == None
    Finish()
  EndIf
EndFunction

Function BeginPending()
  If _speaker == None && _read != _write
    Actor a = _queueA[_read]
    Actor b = _queueB[_read]
    If Owner(a) == None && Owner(b) == None
      _speaker = a
      _listener = b
      _topic = _queueTopic[_read]
      _queueA[_read] = None
      _queueB[_read] = None
      _queueTopic[_read] = None
      _read = (_read + 1) % 32
      Mark(a, GetFormID())
      Mark(b, GetFormID())
      _lastInfo = 0
      _choice = 0
      _nextSpeaker = 1
      _steps = 0
    EndIf
  EndIf
EndFunction

Function TryTopic()
  If _speaker != None && !_waiting
    If _steps >= 256
      Debug.Trace("TES4 conversation exceeded its routing limit")
      Finish()
    ElseIf _topic == None
      Finish()
    ElseIf _speaker == Game.GetPlayer()
      AdvanceCandidate()
    Else
      _waiting = True
      _began = False
      _startedInfo = 0
      _deadline = Utility.GetCurrentRealTime() + 2.0
      Float accepted = TES4Polyfill.SayLineNoWait(_speaker, _topic, 4.0)
      If accepted == 0.5
        ; A busy actor has not rejected this topic: retry it on the next poll.
        _waiting = False
      Else
        _steps += 1
      EndIf
    EndIf
  EndIf
EndFunction

Event OnUpdate()
  If _updating
    RegisterForSingleUpdate(0.1)
    Return
  EndIf
  _updating = True
  EnsureArrays()
  BeginPending()
  If _speaker != None
    If _endedInfo != 0
      _choice = 0
      _eitherTried = False
      _lastInfo = _endedInfo
      _nextSpeaker = Route(_lastInfo)
      _endedInfo = 0
      _waiting = False
      _began = False
      If _nextSpeaker == 0 || _nextSpeaker == 2
        Actor swap = _speaker
        _speaker = _listener
        _listener = swap
      EndIf
      _topic = NextChoice(_lastInfo, 0)
      If _topic == None
        Finish()
      EndIf
    ElseIf _waiting && Utility.GetCurrentRealTime() >= _deadline
      If _began
        ; A missing End must not fabricate the result or continue the quest.
        Debug.Trace("TES4 conversation timed out waiting for an INFO End")
        Finish()
      Else
        _waiting = False
        AdvanceCandidate()
      EndIf
    EndIf
    TryTopic()
  EndIf
  _updating = False
  If _speaker != None || _read != _write
    RegisterForSingleUpdate(0.1)
  EndIf
EndEvent
