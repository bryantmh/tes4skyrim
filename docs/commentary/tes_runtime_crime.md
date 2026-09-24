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

## <a id="arrest-force-greet"></a>The arrest

Skyrim's pursuing guard opens dialogue through a ForceGreet-subtype topic
(`PFGT`, vanilla `DGCrimeForcegreetTopic`). A guard is a class with the Guard
flag (TES5 CLAS DATA's last byte; set on exactly GuardImperial, GuardOrc1H,
GuardOrc2H and GuardSonsSkyrim) in `IsGuardFaction` (Skyrim.esm `0x86EEE`).
The import copies each guard-only GREETING (see
[tes5_import_dialogue.md](tes5_import_dialogue.md#arrest-force-greet)); a
Morrowind guard gets one blank `PFGT` line that the Morrowind runtime diverts
(see [morrowind_runtime.md](morrowind_runtime.md#crime-is-the-engines)).
