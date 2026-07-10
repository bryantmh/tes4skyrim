ScriptName TES4_ShowBarterMenu extends TopicInfo Hidden
{Shared fragment for converted TES4 Barter-topic INFOs: opens the Skyrim
barter menu when the vendor's line finishes. The speaker's synthesized
vendor faction (VEND keyword list) filters what they buy/sell.}

Function Fragment_0(ObjectReference akSpeakerRef)
  (akSpeakerRef as Actor).ShowBarterMenu()
EndFunction
