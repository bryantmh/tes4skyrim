# MorrowindRuntime opcode test plan — TR_Mainland.esm

**Tool:** `python -m tools.dialog.morrowind_opcode_testplan --plugin TR_Mainland.esm --stubs --markdown <this file>`

Measured over 1323 journal quest(s) with stages. The cover is greedy on new-commands-per-run, prerequisites included.

## Ported commands (167 of 326 covered)

| # | Quest | Quest giver | `coc` target / location | Stages | Prerequisite | Commands it is first to test |
|--:|---|---|---|--:|---|---|
| 1 | Caught Off-Guard | TR_m3_Relamus_Saravyne | Bosmora, Relamus Saravyne's Quarters | 25 | — | `additem`, `aifollow`, `aitravel`, `aiwander`, `cast`, `cellchanged`, `choice`, `clearforcesneak`, `disable`, `enable`, `forcegreeting`, `forcesneak`, `getaipackagedone`, `getdeadcount`, `getdisabled`, `getdisposition`, `getdistance`, `geteffect`, `gethandtohand`, `gethello`, `getinvisible`, `getjournalindex`, `getlevel`, `getlocked`, `getparalysis`, `getpccell`, `getpccrimelevel`, `getpcsleep`, `getpos`, `getsecondspassed`, `getstrength`, `goodbye`, `journal`, `menumode`, `modcurrentfatigue`, `moddisposition`, `modpccrimelevel`, `ondeath`, `pcexpell`, `placeitemcell`, `playsound3d`, `removeeffects`, `removespelleffects`, `samefaction`, `setdelete`, `setfatigue`, `setfight`, `sethello`, `setjournalindex`, `setparalysis`, `setpccrimelevel`, `setspeed`, `startcombat`, `startscript`, `stopcombat`, `stopscript`, `unlock` |
| 2 | Walk the Talk | TR_m2_Hozgub gro-Hazor | The Inn Between | 30 | Fighters Guild: Belated Service | `activate`, `addspell`, `addtopic`, `drop`, `getalchemy`, `getarmorer`, `getconjuration`, `getfatigue`, `getfight`, `gethealth`, `getintelligence`, `getinterior`, `getitemcount`, `getlos`, `getluck`, `getmagicka`, `getmercantile`, `getmysticism`, `getpcrank`, `getpcsneaking`, `getrace`, `getrestoration`, `getweapondrawn`, `modagility`, `modcurrenthealth`, `modfatigue`, `modfight`, `modflee`, `modhealth`, `modmagicka`, `modpcfacrep`, `modreputation`, `onactivate`, `pcclearexpelled`, `pcjoinfaction`, `pcraiserank`, `placeatpc`, `playsound`, `positioncell`, `random`, `removeitem`, `removespell`, `setalarm`, `setdisposition`, `sethandtohand`, `sethealth`, `setmercantile`, `setpcfacrep`, `setpos` |
| 3 | Baluath: The Ageless Apprentice | TR_m4_V_Ofelia | Khirakai | 7 | — | `equip`, `getchameleon`, `getcurrentweather`, `getspell`, `getspelleffects`, `modchameleon`, `modrestoration`, `scriptrunning`, `setchameleon` |
| 4 | The Coward and the Tomb | TR_m1_Q_UneleasRangirth | MolagreahdRegionX54Y16 (54, 16) | 21 | — | `getsoundplaying`, `modmarksman`, `move`, `playloopsound3dvp`, `rotateworld` |
| 5 | A Nirn-Bound Saint | TR_m4_Cr_AurmazlKaari | RothRorynRegionXN16YN35 (-16, -35) | 11 | — | `explodespell`, `gettarget`, `placeatme`, `playsoundvp`, `resurrect` |
| 6 | Mages Guild: Deal with a Devil | TR_m1_Absolon | Firewatch, College: Library Tower | 7 | — | `addsoulgem`, `aifollowcell`, `hassoulgem`, `removesoulgem` |
| 7 | Narsis Arena: The Ebony Scale | TR_m7_Felms | Narsis, Arena | 9 | — | `setblock`, `setlongblade`, `setmarksman`, `setmediumarmor` |
| 8 | Temple: Pilgrimage to Muatra's Pedestal | script: TR_m7_Pedestal_Muatra_sc | ? | 1 | — | `getdestruction`, `getspear`, `moddestruction`, `modspear` |
| 9 | The Slave Whisperer | TR_m4_q_S_dur_juu | AanthirinRegionX8YN40 (8, -40) | 19 | — | `getcurrentaipackage`, `getspeechcraft`, `lock` |
| 10 | House Telvanni: Uncharted Waters | TR_m1_T_Malvas_Relvani | Port Telvannis, Telvanni Council House: Chambers | 9 | — | `getangle`, `pclowerrank`, `setangle` |
| 11 | Free at Last? | TR_m2_Midave_Llarys | Erethan Plantation (63, -15) | 27 | — | `setaxe`, `setflee`, `setshortblade` |
| 12 | Mages Guild: Interview with a Vampire | TR_m2_Ranosa_Orrels | Akamora, Guild of Mages | 15 | — | `getspellreadied`, `modfactionreaction`, `modmercantile` |
| 13 | Mages Guild: Altered Erratum | TR_m7_Malvyn Tinur | Narsis, Foreign Quarter (14, -103) | 4 | — | `modacrobatics`, `modalteration`, `placeitem` |
| 14 | Mages Guild: Enchanted Erratum | TR_m7_Anatolius Datus | Narsis, Guild of Mages: Genatorium | 6 | — | `modcurrentmagicka`, `playloopsound3d`, `stopsound` |
| 15 | The Nameless Dunmer | TR_m2_q_38_favryn | Akamora, The Laughing Goblin | 25 | — | `getlongblade`, `position` |
| 16 | Fighters Guild: Recover the Map of Ushu-Dimmu | TR_m4_Garonag gro-Muk | Mvelthngth-Schel (-26, -41) | 6 | — | `face`, `fall` |
| 17 | Atriban's Metamorphosis | TR_m4_Llaals Sathrobar | Arvud (-7, -52) | 11 | Temple: Armun Ashlands Avenger | `aiescort`, `setagility`, `setwillpower` |
| 18 | Religious Inquiry | TR_m3_Girynu_Rathryon | Sailen, Pilgrim Hostel | 13 | — | `modconjuration` |
| 19 | Imperial Legion: Shipment to Nivalis | TR_m1_Darnell | Firewatch, Eastern Watchtower | 3 | — | `modlongblade` |
| 20 | Which Witch? | TR_m2_q_12_Rianele_Sele | MolagRuhnRegionX56YN2 (56, -2) | 12 | — | `modaxe` |
| 21 | Ghoulish Business | TR_m3_Helice Rastin | Ebon Tower, Arkay's Tower | 10 | — | `getdetected` |
| 22 | A Smuggler Found | TR_m1_Himnatis | Llothanis, The Water's Shadow Tavern | 6 | — | `aiactivate` |

**Called by TR, but never from a quest** — these sit on ambient object scripts (doors, cranks, lights), so they need a hand-written probe rather than a quest:

`moveworld` (363), `rotate` (206), `playsound3dvp` (85), `setscale` (42), `setmagicka` (33), `hasitemequipped` (20), `getsquareroot` (16), `getscale` (15), `getsecurity` (9), `setstrength` (7), `modalchemy` (6), `getwillpower` (6), `getpcrunning` (6), `pcexpelled` (5), `getillusion` (5), `getforcesneak` (5), `setathletics` (4), `modscale` (4), `getpersonality` (4), `getalarm` (4), `setbluntweapon` (3), `setacrobatics` (3), `modshortblade` (3), `getspeed` (3), `getsneak` (3), `getendurance` (3), `getalteration` (3), `setwaterbreathing` (2), `modspeechcraft` (2), `modenchant` (2), `modalarm` (2), `setsneak` (1), `setluck` (1), `setendurance` (1), `setdestruction` (1), `setarmorbonus` (1), `modunarmored` (1), `modstrength` (1), `modpersonality` (1), `modmysticism` (1), `modmediumarmor` (1), `modbluntweapon` (1), `getresistmagicka` (1), `getreputation` (1), `getflee` (1), `getagility` (1), `aiescortcell` (1)

**No call site anywhere in this plugin** (112) — untestable from TR at all; they need a different plugin:

`dropsoulgem`, `getacrobatics`, `getarmorbonus`, `getathletics`, `getattackbonus`, `getaxe`, `getblindness`, `getblock`, `getbluntweapon`, `getcastpenalty`, `getdefendbonus`, `getenchant`, `getfactionreaction`, `getflying`, `getheavyarmor`, `getlightarmor`, `getlineofsight`, `getmarksman`, `getmediumarmor`, `getpcfacrep`, `getresistblight`, `getresistcorprus`, `getresistdisease`, `getresistfire`, `getresistfrost`, `getresistnormalweapons`, `getresistparalysis`, `getresistpoison`, `getresistshock`, `getshortblade`, `getsilence`, `getsuperjump`, `getswimspeed`, `getunarmored`, `getwaterbreathing`, `getwaterwalking`, `modarmorbonus`, `modarmorer`, `modathletics`, `modattackbonus`, `modblindness`, `modblock`, `modcastpenalty`, `moddefendbonus`, `modendurance`, `modflying`, `modhandtohand`, `modheavyarmor`, `modhello`, `modillusion`, `modintelligence`, `modinvisible`, `modlightarmor`, `modluck`, `modparalysis`, `modresistblight`, `modresistcorprus`, `modresistdisease`, `modresistfire`, `modresistfrost`, `modresistmagicka`, `modresistnormalweapons`, `modresistparalysis`, `modresistpoison`, `modresistshock`, `modsecurity`, `modsilence`, `modsneak`, `modspeed`, `modsuperjump`, `modswimspeed`, `modwaterbreathing`, `modwaterwalking`, `modwillpower`, `setalchemy`, `setalteration`, `setarmorer`, `setattackbonus`, `setblindness`, `setcastpenalty`, `setconjuration`, `setdefendbonus`, `setenchant`, `setfactionreaction`, `setflying`, `setheavyarmor`, `setillusion`, `setintelligence`, `setinvisible`, `setlightarmor`, `setmysticism`, `setpersonality`, `setreputation`, `setresistblight`, `setresistcorprus`, `setresistdisease`, `setresistfire`, `setresistfrost`, `setresistmagicka`, `setresistnormalweapons`, `setresistparalysis`, `setresistpoison`, `setresistshock`, `setrestoration`, `setsecurity`, `setsilence`, `setspear`, `setspeechcraft`, `setsuperjump`, `setswimspeed`, `setunarmored`, `setwaterwalking`

## Stubbed commands — registered but doing nothing (29 of 45 covered)

Running these routes the player through commands that compile and dispatch but do nothing, so the log names the silent no-op behind each broken stage.

| # | Quest | Quest giver | `coc` target / location | Stages | Prerequisite | Commands it is first to test |
|--:|---|---|---|--:|---|---|
| 1 | House Telvanni: Uncharted Waters | TR_m1_T_Malvas_Relvani | Port Telvannis, Telvanni Council House: Chambers | 9 | — | `disablelevitation`, `disableteleporting`, `enablelevitation`, `enableteleporting`, `say` |
| 2 | Caught Off-Guard | TR_m3_Relamus_Saravyne | Bosmora, Relamus Saravyne's Quarters | 25 | — | `getbuttonpressed`, `onknockout`, `wakeuppc` |
| 3 | Walk the Talk | TR_m2_Hozgub gro-Hazor | The Inn Between | 30 | Fighters Guild: Belated Service | `getweapontype`, `gotojail`, `lowerrank`, `payfine`, `payfinethief`, `playgroup` |
| 4 | The Coward and the Tomb | TR_m1_Q_UneleasRangirth | MolagreahdRegionX54Y16 (54, 16) | 21 | — | `setatstart` |
| 5 | The Many Scamps of Mannu | TR_m4_Atreno Drenim | Mannu | 9 | — | `getattacked` |
| 6 | The Rift | TR_m3_Ralam_Othravel | AltOrethanRegionX28YN64 (28, -64) | 18 | — | `modwaterlevel` |
| 7 | The Slave Whisperer | TR_m4_q_S_dur_juu | AanthirinRegionX8YN40 (8, -40) | 19 | — | `saydone` |
| 8 | The Nameless Dunmer | TR_m2_q_38_favryn | Akamora, The Laughing Goblin | 25 | — | `streammusic` |
| 9 | Intrigue in Port Telvannis | TR_m1_Rantela_Irenam | Port Telvannis, Irenam Manor | 15 | — | `getarmortype` |
| 10 | Pack RATS!!! | TR_m1_Madar_Senatam | Port Telvannis, Madar Senatam's House | 18 | — | `getcommondisease` |
| 11 | Fighters Guild: Tainted Goods | TR_m2_Hartise | Helnim, Guild of Fighters | 13 | — | `loopgroup` |
| 12 | Baluath: The Solitary Savant | TR_m4_V_Ofelia | Khirakai | 7 | — | `getwindspeed` |
| 13 | Mages Guild: Enchanted Erratum | TR_m7_Anatolius Datus | Narsis, Guild of Mages: Genatorium | 6 | — | `hurtcollidingactor` |
| 14 | Born a Necromancer | TR_m7_Belashu Kels | Ussiran Camp, Nind | 7 | — | `getcurrenttime` |
| 15 | House Telvanni: Fools That Meddle | TR_m1_T_Norahin_Darys | Port Telvannis, Telvanni Council House: Chambers | 6 | House Telvanni: Unseating Rilvin Dral | `onmurder` |
| 16 | Fighters Guild: A Blight on Business | TR_m2_Hartise | Helnim, Guild of Fighters | 6 | Thieves Guild: Deal with Disela | `getblightdisease` |
| 17 | Shadows Under Aimrah | TR_m3_Dava Arenim | Aimrah, Communal Tradehouse | 1 | House Hlaalu: The Spirit of Neen | `changeweather` |
| 18 | Temple: The Last Will and Testament of Ulmon Vathri | TR_m4_Gadani Pathavel | Bal Foyen, Temple | 1 | The Statue, Temple: Kitchen Supplies | `raiserank` |

**Called by TR, but never from a quest** — these sit on ambient object scripts (doors, cranks, lights), so they need a hand-written probe rather than a quest:

`getwaterlevel` (15), `getstandingactor` (14), `removefromlevcreature` (13), `setwaterlevel` (12), `getpcjumping` (11), `getstandingpc` (10), `showrestmenu` (9), `getcollidingpc` (9), `resetactors` (6), `hurtstandingactor` (5), `dontsaveobject` (5), `togglemenus` (4), `skipanim` (3), `menutest` (3), `getstartingangle` (3), `hitonme` (1)

