# TESRuntime: jails and bounty realms

**Code:** `tes_runtime/plugin/crime.cpp`, `tes5_import/record_types/crime.py`,
`tes5_import/dialogue/arrest.py`, `script_convert/static_scripts/TES4Polyfill.psc`
(`CrimeFaction`, `CrimeRealm`, `SetCrimeRealm`).

Not yet confirmed in game.

## <a id="bounty-realms"></a>One bounty per realm

Skyrim records a crime against the victim's crime faction (NPC_ `CRIF`) and a
witness without one only warns or fights (CK wiki, *Crime*). Oblivion and
Morrowind keep ONE bounty for the whole game, except that Oblivion's Shivering
Isles doors call `SetPlayerInSEWorld 1/0` (`SEDoorToShiveringIslesScript`,
`SEDoorToTamrielSCRIPT`, `SE01WaitingDoorScript`) and the engine swaps the
bounty on that flag.

The import therefore makes one Track Crime faction per **realm**: the cells a
plugin's own root worldspaces reach through teleport doors, cut at a door pair
whose script toggles `SetPlayerInSEWorld`; the side a `1` door leads to is a
second realm. A plugin with no root worldspace of its own shares its nearest
master's main realm, which is how Morroblivion's `Morrowind_ob.esm` (own
`WrldMorrowind`/`WrldMournhold`) gets its own bounty while TR and the compat
patch join it.

A master's worldspace also counts as the plugin's own when the conversion
renames it for that plugin (`core/worldspace_names.py`): Arktwend is built on
Morrowind's exterior grid, but its `WrldMorrowind` converts to `WrldArktwend`
("Arktwend"), so Arktwend gets its own bounty. A rename every chain shares
(Tamriel -> `TES4Tamriel`) claims nothing. Cells no door reaches take the
plugin's own main realm when it has one. Measured on the exports:

| Plugin | Realms | Cells | NPCs assigned | Jails |
|---|---|---|---|---|
| Oblivion.esm | main / SEWorld | 30,416 / 4,784 | 1,596 / 326 | 10 / 4 |
| Nehrim.esm | main | 11,156 | 1,700 | 7 |
| Morrowind_ob.esm | main | 11,850 | 3,019 | (all main) |
| TR_Mainland.esm | Morrowind_ob's | 11,252 | 8,522 | 16 |
| Arktwend_English.esm | main ("Arktwend") | 946 | -- | 0 (none authored) |

Every NPC_ joins and reports to the realm of its first placement's cell, except
one that attacks the player on sight (aggression tier 2+), which is no victim.

Converted scripts read and write the realm the player is in through the
plugin's FormList `TES4CrimeFactions_<plugin>` (main realm first).
`SetPlayerInSEWorld 1` adds the list itself as a marker: `FormList.AddForm`
refuses a form already in the list (1.6.1170 `0x319e20` scans the base array
first), `GetAt` returns script-added forms before the authored ones
(`0x319bb0` reads `+0x38` first), and `HasForm` checks both (`0x319ae0`).

## <a id="nearest-jail"></a>The jail is the nearest enabled prison marker

Both source engines jail the player at the nearest enabled prison marker:
OpenMW `World::getClosestMarker("prisonmarker")`, and Oblivion's Isles door
disables `SEJailMarkerParentTamriel` and enables the Isles markers to change
jails. A jail is a prison marker whose teleport lands in another interior cell;
its evidence chest is the engine's stolen-goods container in that cell
(Oblivion `StolenGoods` 0x11, TES3 `stolen_goods`, Morroblivion
`0stolenUgoods` -- the same id after undoing Morroblivion's mangling).

Skyrim instead fixes one jail per crime faction, in `TESFaction` crime data:

| Field | Offset | Record |
|---|---|---|
| Exterior jail marker | `+0x60` | JAIL |
| Follower wait marker | `+0x68` | WAIT |
| Stolen goods container | `+0x70` | STOL |
| Player inventory container | `+0x78` | PLCN |

`TESFaction::Load` (1.6.1170 `0x3ac9c0`) stores each subrecord's FormID there
and `InitItem` (`0x3ad2e0`, vtable `0x17e1fc0` slot 19) replaces it with the
`TESObjectREFR*` (LookupFormByID + dynamic cast). So the DLL, every two
seconds on the main thread, finds the enabled jail nearest the player in the
same root worldspace -- inside, from the spot the interior's door opens onto --
and writes that marker and chest into every converted crime faction. The
markers and chests are persistent, so they resolve at any distance.

The ref a marker's `XTEL` names must be persistent too. `SendPlayerToJail`
(1.6.1170 `0x747200`, id 40655) reads the marker's `ExtraTeleport` (extra type
`0x2B`, `0x2faa90`) and dereferences it unchecked. The engine resolves that
linked ref when it loads the marker (CK: "Could not find linked door (%08X) in
teleport data init."), and a temporary ref in an unloaded interior does not
exist yet, so the marker ends up with no teleport and the arrest crashes at
`+0x1FE`. Vanilla, Oblivion and Morroblivion pair two persistent prison
markers. TR_Mainland sends 13 of its 16 markers to the jail's temporary door,
so the import gives each one a persistent partner
([below](#return-marker)).

## <a id="unloaded-plugin-skipped"></a>A crime file whose plugin is not loaded is skipped

Each `<plugin>.crime.json` is kept whole until DataLoaded and asked once
whether its own plugin is loaded: its first faction, jail marker or anchor cell
naming that plugin's own file must resolve. Otherwise the whole file is
skipped and logged (`crime: X is not loaded -- its crime file is skipped`).
Before this, every row went to `GetFormFromFile`, and each miss is an engine
error in the Papyrus log. Measured: 9,349 errors in one second from the
Morrowind_ob and TR_Mainland files with neither plugin enabled. Worldspace roots
are gathered from every loaded file before any file's anchors resolve, since an
anchor's worldspace is often a master's. A file naming no form of its own
resolves as before.

## <a id="serve-time"></a>Serving time keeps the stolen goods

Both source games keep confiscated stolen goods in the evidence chest and hand
everything else back when the sentence is served (UESP, *Oblivion:Crime*;
OpenMW `World::confiscateStolenItems`). Skyrim splits the two at the arrest
instead: `0x74cc90` moves stolen items to the faction's stolen-goods chest
(`+0x70`) and the rest to its player-inventory chest (`+0x78`), and ServeTime
hands back all of `+0x78`. Both authored games have one chest, so both slots
name it, and ServeTime handed the stolen goods back too.

ServeTime is PlayerCharacter vtable slot 186 (1.6.1170 `0x747740`, id 40657).
Nothing calls it directly: `Game.ServeTime` tail-calls the slot, and so does
sleeping in jail. Its first call fades out and sets bit `0x10` of `+0xbe5`,
and its second passes the days and releases the player. TESRuntime swaps the
slot. After a second call for one of its crime factions, it runs the engine's
own confiscation, `PayCrimeGold(faction, goToJail=false, removeStolen=true)`
(slot 187, `0x747a10`), which with the bounty already cleared takes no gold.

### <a id="return-marker"></a>A jail marker needs a partner that teleports back

ServeTime's first call raises the loading screen (`0x1a1150(1, location, ...)`).
Its release (`0x747e00`) takes the jail marker's linked ref and queues a move
to that ref's OWN teleport target, and the move's load is what takes the
screen down again (`0x1a1150(0, null, 0, 1)`). When the linked ref has no
teleport, there is no move and the screen stays up for good. The release has
no other exit.

Vanilla pairs two persistent prison markers that teleport to each other, and
so do Oblivion's jails. Of TR_Mainland's 16 jail markers, 13 lead to a jail
door with no teleport. For TR's Bal Foyen garrison, the live game showed the
sentence served (PlayerCharacter `+0x720` cleared, `+0x728` 0), the player at
the marker's landing spot, no move queued, and the loading screen still up.

So the import (`_link_jail_returns`) gives every jail marker whose target does
not teleport back a new persistent marker: a copy of it at its landing spot,
teleporting back to it. It then points the marker's XTEL at the copy. The
released prisoner lands at the jail marker, as in Skyrim. The id is keyed on
the authored marker.

## <a id="arrest-force-greet"></a>The arrest

Skyrim's pursuing guard opens dialogue through a ForceGreet-subtype topic
(`PFGT`, vanilla `DGCrimeForcegreetTopic`). A guard is a class with the Guard
flag (TES5 CLAS DATA's last byte; set on exactly GuardImperial, GuardOrc1H,
GuardOrc2H and GuardSonsSkyrim) in `IsGuardFaction` (Skyrim.esm `0x86EEE`).
The import copies each guard-only GREETING (see
[tes5_import_dialogue.md](tes5_import_dialogue.md#arrest-force-greet)); a
Morrowind guard gets one blank `PFGT` line that the Morrowind runtime diverts
(see [morrowind_runtime.md](morrowind_runtime.md#crime-is-the-engines)).
