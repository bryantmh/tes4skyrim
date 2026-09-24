// Jails: every converted crime faction sends the player to the nearest jail.
//
// Oblivion and Morrowind both jail an arrested player at the nearest ENABLED
// prison marker (OpenMW getClosestMarker; Oblivion's Shivering Isles door
// switches jails by enabling and disabling markers). Skyrim fixes one jail per
// crime faction instead. The importer writes each plugin's jails, interior
// anchors and crime factions to <plugin>.crime.json; every few seconds this
// points every listed faction's jail, follower-wait marker and evidence chests
// at the enabled jail nearest the player, so the engine's own
// SendPlayerToJail and PlayerPayCrimeGold land where the source game would.
// See: docs/commentary/tes_runtime_crime.md#nearest-jail

#pragma once

namespace tesruntime {

// Reads every *.crime.json. False when there is nothing to do.
bool LoadCrimeSidecars();

// After DataLoaded: resolves factions, jails and anchors to live forms.
void ResolveCrimeForms();

// Starts the background timer that re-points the jails on the main thread.
void StartCrimeTick();

}  // namespace tesruntime
