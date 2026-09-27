ScriptName TES4_DayClock extends Quest Hidden
{Keeps TES4GameDaysPassed equal to the whole days in Skyrim's GameDaysPassed.
Oblivion's GameDaysPassed only counts whole days, while Skyrim's carries the
fraction of the current day, so converted conditions that compare a stored day
against it read TES4GameDaysPassed instead. Refreshed at quest start and at
every midnight.}

GlobalVariable Property GameDaysPassed Auto
GlobalVariable Property TES4GameDaysPassed Auto

; Game hours past midnight to wake at, so a float shortfall never wakes early.
Float Property WakeMargin = 0.01 AutoReadOnly

Event OnInit()
  Tick()
EndEvent

Event OnUpdateGameTime()
  Tick()
EndEvent

Function Tick()
  Float now = GameDaysPassed.GetValue()
  Int day = now as Int
  TES4GameDaysPassed.SetValue(day)
  RegisterForSingleUpdateGameTime((day + 1 - now) * 24.0 + WakeMargin)
EndFunction
