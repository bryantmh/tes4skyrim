# MorrowindRuntime opcode test plan — TR_Mainland.esm

**Tool:** `python -m tools.dialog.morrowind_opcode_testplan --plugin TR_Mainland.esm --stubs --markdown <this file>`

Measured over 1323 journal quest(s) with stages. Ranked by new commands per STAGE the tester must play, so short quests come first. **A prerequisite is its own row**, marked in *Why*, and its stages and commands both count -- the table is the whole play order, top to bottom.

## Ported commands (112 of 119 covered)

| # | Quest | Quest giver | `coc` target / location | Stages | Why | Commands it is first to test |
|--:|---|---|---|--:|---|---|
| 1 | A Nirn-Bound Saint | TR_m4_Q_NirnboundBoulder (TR_m4_NirnboundBoulder_Scp) | RothRorynRegionXN16YN35 (-16, -35) | 1 | — | `cast`, `disable`, `explodespell`, `getdisabled`, `getjournalindex`, `getsecondspassed`, `journal`, `placeatme`, `playsound`, `playsoundvp`, `rotateworld` |
| 2 | Temple: Armun Ashlands Avenger | TR_m4_D_KhirakaiBandit1 (TR_m4_TT_ArmunAdvC_sc) | Khirakai | 2 | — | `activate`, `additem`, `addspell`, `cellchanged`, `equip`, `getcurrentweather`, `getspell`, `getspelleffects`, `menumode`, `removeitem`, `removespell`, `scriptrunning`, `startscript` |
| 3 | Fighters Guild: Alit Trouble in Menaan | TR_m4_Darra (TR_m4_NPC_Darra) | Menaan (-5, -40) | 1 | — | `aiescort`, `aitravel`, `aiwander`, `forcegreeting`, `getaipackagedone`, `getdistance`, `positioncell` |
| 4 | tr_m7_shin_ganettukill | TR_m7_AnatEzharHaddi (TR_m7_Shin_GanEttuKill_sc) | Adammurbael | 2 | — | `getsoundplaying`, `playloopsound3d`, `stopsound` |
| 5 | Mages Guild: A Rare Enchantment | TR_m2_Ranosa_Orrels | Akamora, Guild of Mages | 3 | — | `addtopic`, `choice`, `getdeadcount`, `getitemcount`, `getpcrank`, `getpcsneaking`, `goodbye`, `moddisposition`, `modfactionreaction`, `modpcfacrep`, `placeitemcell` |
| 6 | Fighters Guild: Recover the Map of Ushu-Dimmu | TR_m4_Garonag gro-Muk | Mvelthngth-Schel (-26, -41) | 6 | — | `face`, `fall`, `getfatigue`, `getpos`, `modcurrentfatigue`, `say`, `setpos`, `startcombat` |
| 7 | House Telvanni: Ingredients for Lord Dral | TR_m1_T_Malvas_Relvani | Port Telvannis, Telvanni Council House: Chambers | 2 | — | `pclowerrank` |
| 8 | Imperial Legion: Firemoth Rekindled | Galas Drenim | EbonheartVSGrandSCouncilSChambers | 2 | — | `addsoulgem`, `modreputation`, `pcraiserank` |
| 9 | tr_m7_ns_casino_prisoner | TR_m7_Mirnelea Llothan | Narsis, Sewers: Waterfront Hideout | 2 | — | `getlocked` |
| 10 | Mages Guild: Altered Erratum | TR_m7_Malvyn Tinur | Narsis, Foreign Quarter (14, -103) | 4 | — | `modacrobatics`, `placeitem` |
| 11 | Shadows Under Aimrah | TR_m3_Ulvo Telvor | Aimrah, Lighthouse | 4 | — | `setscale` |
| 12 | Lost in Transit | TR_m4_Yakasamshi | Ernabapalit Camp, Yakasamshi's Yurt | 4 | — | `aiescortcell`, `getfight`, `getinterior`, `getpccell`, `getpcsleep`, `setalarm`, `stopscript` |
| 13 | The Slave Whisperer | TR_m4_q_S_dur_juu | AanthirinRegionX8YN40 (8, -40) | 4 | — | `getdisposition`, `getlevel`, `setdisposition` |
| 14 | Mages Guild: Restored Erratum | TR_m7_Konn | Narsis, Guild of Mages: Arboretum | 5 | — | `modalchemy`, `setfatigue` |
| 15 | A Smuggler Found | TR_m1_Himnatis | Llothanis, The Water's Shadow Tavern | 6 | — | `aiactivate`, `getangle`, `setdelete`, `stopcombat`, `unlock` |
| 16 | Intrigue in Port Telvannis | TR_m1_Rantela_Irenam | Port Telvannis, Irenam Manor | 6 | — | `drop`, `getalchemy`, `getpccrimelevel`, `getrace`, `modpccrimelevel`, `pcclearexpelled`, `pcjoinfaction`, `playloopsound3dvp`, `random`, `setjournalindex` |
| 17 | Imperial Cult: The Sword of Taldeus | TR_m1_Tarom | Firewatch, Grand Chapel of Akatosh | 8 | — | `move` |
| 18 | The Company We Keep | TR_m3_Relamus_Saravyne | Bosmora, Relamus Saravyne's Quarters | 8 | — | `samefaction` |
| 19 | Imperial Legion: Sabotage | TR_m3_Olfvur Steel-Skin | Ebon Tower, Legion: Headquarters | 8 | — | `getspellreadied`, `removespelleffects` |
| 20 | Flin Galore! | TR_m2_Merro_Galvix | Andar Mok, Andalas Tradehouse | 12 | — | `clearforcesneak`, `forcesneak`, `getcurrentaipackage`, `getdetected`, `getforcesneak` |
| 21 | The Exiled Duke's Affair | TR_m3_Phyrios Mattimus | Ebon Tower, Palace: High Chambers | 29 | — | `getchameleon`, `hasitemequipped` |
| 22 | A Broken Family | TR_m2_Velyn Alari | Sadalvel Ancestral Tomb | 19 | — | `modalarm` |
| 23 | House Hlaalu: Money Never Sleeps | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 10 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 24 | House Hlaalu: Dealing with Orvas Dren | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 3 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 25 | House Hlaalu: Expel the Outlanders | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 3 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 26 | House Hlaalu: Hlaalu Grandmaster | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 2 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 27 | House Hlaalu: Finding the Saint | TR_m7_FatFuckFrank | Narsis, Second Family Manor: Grandmaster Quarters | 9 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 28 | House Hlaalu: The Ritual of St. Veloth | TR_m7_FatFuckFrank | Narsis, Second Family Manor: Grandmaster Quarters | 5 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | `geteffect`, `removeeffects` |
| 29 | House Hlaalu: The Face of Veloth | TR_m7_FatFuckFrank | Narsis, Second Family Manor: Grandmaster Quarters | 4 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | `getsquareroot`, `saydone`, `setangle` |
| 30 | Temple: Pilgrimage of the Saint's Rescue | TR_m7_HH_ogrim_ritual | Kalkusara, Shrine | 2 | — | — |
| 31 | House Hlaalu: Crackdown | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 2 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 32 | Thieves Guild: Flying Too Close | TR_m7_Cervo Cantaber | Shipal-Sharai, Cervo Cantaber: Master Arbalest | 3 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 33 | Thieves Guild: To the Limit | TR_m7_Thorleif | Narsis, Redwater Theater | 4 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 34 | Thieves Guild: Before the Dust Settles | TR_m7_Thorleif | Narsis, Redwater Theater | 4 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 35 | Thieves Guild: Flying Too Close | TR_m7_Thorleif | Narsis, Redwater Theater | 5 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 36 | Thieves Guild: Born to Be Wild | TR_m7_Thorleif | Narsis, Redwater Theater | 4 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 37 | Thieves Guild: The Devil You Know | TR_m7_Thorleif | Narsis, Redwater Theater | 3 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 38 | Thieves Guild: Friends in Low Places | TR_m7_Thorleif | Narsis, Redwater Theater | 10 | unlocks *Ja-Natta Syndicate: Consolidation* | `lock` |
| 39 | Thieves Guild: Offer They Can't Refuse | TR_m7_Thorleif | Narsis, Redwater Theater | 3 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 40 | Thieves Guild: Show Me Your Moves | TR_m7_Thorleif | Narsis, Redwater Theater | 5 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 41 | Thieves Guild: Take the House | TR_m7_Thorleif | Narsis, Redwater Theater | 3 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 42 | Ja-Natta Syndicate: Snake Eyes | TR_m7_JNS_TheBoss | Uddanu, Chamber of K'Vatra | 14 | unlocks *Ja-Natta Syndicate: Consolidation* | `hassoulgem`, `pcexpelled`, `removesoulgem` |
| 43 | Ja-Natta Syndicate: End of the Line | TR_m7_JNS_TheBoss | Uddanu, Chamber of K'Vatra | 7 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 44 | Ja-Natta Syndicate: Consolidation | TR_m7_JNS_TheBoss | Uddanu, Chamber of K'Vatra | 3 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 45 | Ja-Natta Syndicate: Breathing Out | TR_m7_JNS_TheBoss | Uddanu, Chamber of K'Vatra | 4 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 46 | Ja-Natta Syndicate: Breathing In | TR_m7_JNS_TheBoss | Uddanu, Chamber of K'Vatra | 6 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 47 | Ja-Natta Syndicate: Consolidation | TR_m7_JNS_TheBoss | Uddanu, Chamber of K'Vatra | 3 | — | — |
| 48 | Fighters Guild: Conflict of Interest | TR_m2_Amiro | Akamora, Guild of Fighters | 23 | unlocks *tr_m2_fg_akastat* | `position`, `resurrect` |
| 49 | Fighters Guild: No Sin Goes Unpunished | TR_m2_Laalalvo_Irano | Akamora, Guild of Fighters | 13 | unlocks *tr_m2_fg_akastat* | — |
| 50 | Fighters Guild: Noble Protection | TR_m2_Laalalvo_Irano | Akamora, Guild of Fighters | 23 | unlocks *tr_m2_fg_akastat* | — |
| 51 | tr_m2_fg_akastat | TR_m2_q_29_4_Surol | Akamora (61, -22) | 6 | unlocks *tr_m2_fg_akastat* | — |
| 52 | Fighters Guild: Akavorioc | TR_m2_Laalalvo_Irano | Akamora, Guild of Fighters | 4 | unlocks *tr_m2_fg_akastat* | — |
| 53 | Fighters Guild: Caught in a Web | TR_m2_Laalalvo_Irano | Akamora, Guild of Fighters | 13 | unlocks *tr_m2_fg_akastat* | `getscale` |
| 54 | tr_m2_fg_akastat | TR_m2_q_29_4_Surol | Akamora (61, -22) | 6 | — | — |

**209 further command(s) share a handler with one of these and are covered by testing it** -- `Enable` and `Disable` are one `OpSetEnabled`, every attribute and skill command one `OpStat`. The classes come from the runtime's own registrations, and `OpStat` is split by verb and by whether the stat maps to a Skyrim actor value, since those are genuinely different code paths.

| Tested | Also covers |
|---|---|
| `modalchemy` | `modalteration`, `modarmorbonus`, `modarmorer`, `modaxe`, `modblock`, `modbluntweapon`, `modconjuration`, `moddestruction`, `modenchant`, `modhandtohand`, `modheavyarmor`, `modillusion`, `modinvisible`, `modlightarmor`, `modlongblade`, `modmarksman`, `modmediumarmor`, `modmercantile`, `modmysticism`, `modparalysis`, `modresistdisease`, `modresistfire`, `modresistfrost`, `modresistmagicka`, `modresistpoison`, `modresistshock`, `modrestoration`, `modsecurity`, `modshortblade`, `modsneak`, `modspear`, `modspeechcraft`, `modunarmored`, `modwaterbreathing`, `modwaterwalking`, `setalchemy`, `setalteration`, `setarmorbonus`, `setarmorer`, `setaxe`, `setblock`, `setbluntweapon`, `setconjuration`, `setdestruction`, `setenchant`, `sethandtohand`, `setheavyarmor`, `setillusion`, `setinvisible`, `setlightarmor`, `setlongblade`, `setmarksman`, `setmediumarmor`, `setmercantile`, `setmysticism`, `setparalysis`, `setresistdisease`, `setresistfire`, `setresistfrost`, `setresistmagicka`, `setresistpoison`, `setresistshock`, `setrestoration`, `setsecurity`, `setshortblade`, `setsneak`, `setspear`, `setspeechcraft`, `setunarmored`, `setwaterbreathing`, `setwaterwalking` |
| `modacrobatics` | `modagility`, `modathletics`, `modattackbonus`, `modblindness`, `modcastpenalty`, `modchameleon`, `moddefendbonus`, `modendurance`, `modflying`, `modintelligence`, `modluck`, `modpersonality`, `modresistblight`, `modresistcorprus`, `modresistnormalweapons`, `modresistparalysis`, `modsilence`, `modspeed`, `modstrength`, `modsuperjump`, `modswimspeed`, `modwillpower`, `setacrobatics`, `setagility`, `setathletics`, `setattackbonus`, `setblindness`, `setcastpenalty`, `setchameleon`, `setdefendbonus`, `setendurance`, `setflying`, `setintelligence`, `setluck`, `setpersonality`, `setresistblight`, `setresistcorprus`, `setresistnormalweapons`, `setresistparalysis`, `setsilence`, `setspeed`, `setstrength`, `setsuperjump`, `setswimspeed`, `setwillpower` |
| `getalchemy` | `getalteration`, `getarmorbonus`, `getarmorer`, `getaxe`, `getblock`, `getbluntweapon`, `getconjuration`, `getdestruction`, `getenchant`, `gethandtohand`, `getheavyarmor`, `getillusion`, `getinvisible`, `getlightarmor`, `getlongblade`, `getmarksman`, `getmediumarmor`, `getmercantile`, `getmysticism`, `getparalysis`, `getresistdisease`, `getresistfire`, `getresistfrost`, `getresistmagicka`, `getresistpoison`, `getresistshock`, `getrestoration`, `getsecurity`, `getshortblade`, `getsneak`, `getspear`, `getspeechcraft`, `getunarmored`, `getwaterbreathing`, `getwaterwalking` |
| `getchameleon` | `getacrobatics`, `getagility`, `getathletics`, `getattackbonus`, `getblindness`, `getcastpenalty`, `getdefendbonus`, `getendurance`, `getflying`, `getintelligence`, `getluck`, `getpersonality`, `getresistblight`, `getresistcorprus`, `getresistnormalweapons`, `getresistparalysis`, `getsilence`, `getspeed`, `getstrength`, `getsuperjump`, `getswimspeed`, `getwillpower` |
| `modcurrentfatigue` | `modcurrenthealth`, `modcurrentmagicka`, `modfatigue`, `modhealth`, `modmagicka` |
| `getdetected` | `getlineofsight`, `getlos`, `gettarget` |
| `getfight` | `getalarm`, `getflee`, `gethello` |
| `modalarm` | `modfight`, `modflee`, `modhello` |
| `setalarm` | `setfight`, `setflee`, `sethello` |
| `cellchanged` | `onactivate`, `ondeath` |
| `getfatigue` | `gethealth`, `getmagicka` |
| `setfatigue` | `sethealth`, `setmagicka` |
| `aiescort` | `aifollow` |
| `aiescortcell` | `aifollowcell` |
| `disable` | `enable` |
| `getpcsneaking` | `getpcrunning` |
| `getspellreadied` | `getweapondrawn` |
| `modpccrimelevel` | `setpccrimelevel` |
| `modpcfacrep` | `setpcfacrep` |
| `move` | `moveworld` |
| `pcclearexpelled` | `pcexpell` |
| `placeatme` | `placeatpc` |
| `playloopsound3d` | `playsound3d` |
| `playloopsound3dvp` | `playsound3dvp` |
| `rotateworld` | `rotate` |

**Called by TR, but never from a quest** — these sit on ambient object scripts (doors, cranks, lights), so they need a hand-written probe rather than a quest:

`modscale` (4), `getreputation` (1)

**No call site anywhere in this plugin** (5) — untestable from TR at all; they need a different plugin:

`dropsoulgem`, `getfactionreaction`, `getpcfacrep`, `setfactionreaction`, `setreputation`

## Stubbed commands — registered but doing nothing (27 of 43 covered)

Running these routes the player through commands that compile and dispatch but do nothing, so the log names the silent no-op behind each broken stage.

| # | Quest | Quest giver | `coc` target / location | Stages | Why | Commands it is first to test |
|--:|---|---|---|--:|---|---|
| 1 | Temple: Pilgrimage to the Shrine of Purging | TR_m1_q_TTShrine1 (TR_m1_q_TTShrine1_Script) | MolagreahdRegionX54Y19 (54, 19) | 1 | — | `getbuttonpressed` |
| 2 | Temple: Epidemic in Ranyon-ruhn | TR_m1_Arthal_Eindari (TR_m1_q_TT_5_VampNPCA) | Ranyon-ruhn, Arthal Eindari's House | 1 | — | `getcommondisease` |
| 3 | Shadows Under Aimrah | TR_m3_Dava Arenim | Aimrah, Communal Tradehouse | 1 | — | `changeweather` |
| 4 | Temple: A Troublesome Orc | TR_m4_TT_Ravur_Othravel | Bal Foyen, Temple | 1 | — | `raiserank` |
| 5 | Imperial Legion: Firemoth Rekindled | TR_FM_Batshubzub | Firemoth Legion Fort (-16, -21) | 2 | — | `setatstart` |
| 6 | Temple: Armun Ashlands Avenger | TR_m4_D_KhirakaiBandit1 (TR_m4_TT_ArmunAdvC_sc) | Khirakai | 2 | — | `getwindspeed` |
| 7 | House Telvanni: Uncharted Waters | TR_m1_T_Malvas_Relvani | Port Telvannis, Telvanni Council House: Chambers | 9 | — | `disablelevitation`, `disableteleporting`, `enablelevitation`, `enableteleporting` |
| 8 | Fighters Guild: Recover the Map of Ushu-Dimmu | TR_m4_Garonag gro-Muk | Mvelthngth-Schel (-26, -41) | 6 | — | `getattacked`, `playgroup` |
| 9 | Lost in Transit | TR_m4_Yakasamshi | Ernabapalit Camp, Yakasamshi's Yurt | 4 | — | `getarmortype` |
| 10 | House Telvanni: Fools That Meddle | TR_m1_T_Norahin_Darys | Port Telvannis, Telvanni Council House: Chambers | 6 | — | `onmurder` |
| 11 | House Hlaalu: Omaynis Inn | TR_m4_q_Ervan Indrano | Omaynis (-14, -31) | 6 | — | `gotojail`, `lowerrank`, `onknockout`, `payfine`, `payfinethief` |
| 12 | Mages Guild: Enchanted Erratum | TR_m7_Anatolius Datus | Narsis, Guild of Mages: Genatorium | 6 | — | `hurtcollidingactor` |
| 13 | Born a Necromancer | TR_m7_Belashu Kels | Ussiran Camp, Nind | 7 | — | `getcurrenttime` |
| 14 | Kill or Be Killed | TR_m1_Medenb_Khifzah | Tel Ouada, Midaan Manor | 10 | — | `wakeuppc` |
| 15 | Fighters Guild: Joran the Defector | TR_m2_Hartise | Helnim, Guild of Fighters | 8 | unlocks *Fighters Guild: A Blight on Business* | — |
| 16 | Fighters Guild: Tainted Goods | TR_m2_Hartise | Helnim, Guild of Fighters | 13 | unlocks *Fighters Guild: A Blight on Business* | `loopgroup` |
| 17 | Fighters Guild: A Blight on Business | TR_m2_Hartise | Helnim, Guild of Fighters | 6 | — | `getblightdisease` |
| 18 | The Rift | TR_m3_Ralam_Othravel | AltOrethanRegionX28YN64 (28, -64) | 18 | — | `modwaterlevel` |
| 19 | The Nameless Dunmer | TR_m2_q_38_favryn | Akamora, The Laughing Goblin | 25 | — | `streammusic` |
| 20 | Fighters Guild: Belated Service | TR_m2_Amiro | Akamora, Guild of Fighters | 8 | unlocks *Fighters Guild: Belated Service* | — |
| 21 | Walk the Talk | TR_m2_Hozgub gro-Hazor | The Inn Between | 30 | unlocks *Fighters Guild: Belated Service* | `getweapontype` |
| 22 | Fighters Guild: Belated Service | TR_m2_Amiro | Akamora, Guild of Fighters | 8 | — | — |

**Called by TR, but never from a quest** — these sit on ambient object scripts (doors, cranks, lights), so they need a hand-written probe rather than a quest:

`getwaterlevel` (15), `getstandingactor` (14), `removefromlevcreature` (13), `setwaterlevel` (12), `getpcjumping` (11), `getstandingpc` (10), `showrestmenu` (9), `getcollidingpc` (9), `resetactors` (6), `hurtstandingactor` (5), `dontsaveobject` (5), `togglemenus` (4), `skipanim` (3), `menutest` (3), `getstartingangle` (3), `hitonme` (1)

