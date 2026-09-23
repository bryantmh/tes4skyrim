# MorrowindRuntime opcode test plan — Morrowind.esm

**Tool:** `python -m tools.dialog.morrowind_opcode_testplan --plugin Morrowind.esm --stubs --markdown <this file>`

Measured over 629 journal quest(s) with stages. Ranked by new commands per STAGE the tester must play, so short quests come first. **A prerequisite is its own row**, marked in *Why*, and its stages and commands both count -- the table is the whole play order, top to bottom.

## Ported commands (68 of 126 covered)

| # | Quest | Quest giver | `coc` target / location | Stages | Why | Commands it is first to test |
|--:|---|---|---|--:|---|---|
| 1 | tt_maskvivec | ac_shrine_gnisis (shrineGnisis) | Gnisis, Temple | 1 | — | `cast`, `cellchanged`, `getbuttonpressed`, `getitemcount`, `getjournalindex`, `getsecondspassed`, `journal`, `menumode`, `move`, `playsound3d`, `removeitem`, `setpos` |
| 2 | ms_trerayna_bounty | mollimo of cloudrest | Tel Branora (30, -25) | 2 | — | `additem`, `moddisposition`, `setalarm` |
| 3 | tg_bitterbribe | stacey | Vivec, Simine Fralinie: Bookseller | 7 | — | `activate`, `addtopic`, `choice`, `disable`, `getagility`, `goodbye`, `modagility`, `modpcfacrep`, `pcraiserank`, `startcombat` |
| 4 | ht_eddieamulet | edd theman | Balmora, Fast Eddie's House | 4 | — | `modreputation`, `raiserank`, `startscript`, `stopscript` |
| 5 | ms_lookout | hrisskar flat-foot | Seyda Neen, Arrille's Tradehouse | 5 | — | `aiwander`, `clearforcesneak` |
| 6 | hh_twinlamps1 | jobasha | Vivec, Jobasha's Rare Books | 6 | unlocks *hh_twinlamps3* | `aitravel`, `forcegreeting`, `getaipackagedone`, `getcurrentaipackage`, `getdistance`, `gethealth`, `getpccrimelevel`, `modfight`, `pcclearexpelled`, `pcjoinfaction`, `say`, `setdisposition`, `setpccrimelevel` |
| 7 | hh_twinlamps3 | ilmeni dren | Vivec, St. Delyn Canal South-One | 4 | — | `getpccell`, `getpos`, `saydone` |
| 8 | da_malacath | farvyn oreyn | BitterCoastRegionXN16Y5 (-16, 5) | 7 | — | `getrace` |
| 9 | tt_compassion | tuls valen | Ald-ruhn, Temple | 7 | — | `getdisabled`, `getspelleffects`, `stopcombat` |
| 10 | ms_whiteguar | urshamusa rapli | Ahemmusa Camp (23, 32) | 9 | — | `aiescort` |
| 11 | mv_recoverwidowmaker | botrir | AzurasCoastRegionX26Y18 (26, 18) | 9 | — | `getdeadcount`, `modaxe` |
| 12 | hr_hlaanoslanders | faral retheran | Vivec, Redoran Treasury | 3 | unlocks *hr_cowarddisgrace* | — |
| 13 | hr_redastomb | faral retheran | Vivec, Redoran Treasury | 3 | unlocks *hr_cowarddisgrace* | — |
| 14 | hr_cowarddisgrace | faral retheran | Vivec, Redoran Treasury | 5 | — | `getflee` |
| 15 | mv_monsterdisease | din | WestGashRegionXN23Y26 (-23, 26) | 11 | — | `aifollowcell`, `getspell` |
| 16 | eb_bone | balen andrano | Vivec, Redoran Trader | 13 | — | `getdetected`, `sethealth` |
| 17 | mv_strayedpilgrim | thoronor | AscadianIslesRegionX4YN8 (4, -8) | 19 | — | `geteffect` |
| 18 | mg_joinus | ranis athrys | Balmora, Guild of Mages | 3 | unlocks *mg_killnecro2* | — |
| 19 | mg_paydues | ranis athrys | Balmora, Guild of Mages | 5 | unlocks *mg_killnecro2* | — |
| 20 | mg_stopcompetition | ranis athrys | Balmora, Guild of Mages | 5 | unlocks *mg_killnecro2* | — |
| 21 | mg_escortscholar2 | ranis athrys | Balmora, Guild of Mages | 5 | unlocks *mg_killnecro2* | — |
| 22 | mg_killnecro2 | ranis athrys | Balmora, Guild of Mages | 4 | — | `placeatpc` |
| 23 | a1_1_findspymaster | chargen captain | Seyda Neen, Census and Excise Office | 10 | unlocks *a2_6_incarnate* | `positioncell` |
| 24 | a1_2_antabolisinformant | caius cosades | Balmora, Caius Cosades' House | 7 | unlocks *a2_6_incarnate* | — |
| 25 | a1_4_muzgobinformant | caius cosades | Balmora, Caius Cosades' House | 8 | unlocks *a2_6_incarnate* | `addspell` |
| 26 | a1_v_vivecinformants | caius cosades | Balmora, Caius Cosades' House | 3 | unlocks *a2_6_incarnate* | — |
| 27 | a1_11_zainsubaniinformant | caius cosades | Balmora, Caius Cosades' House | 9 | unlocks *a2_6_incarnate* | — |
| 28 | a2_1_meetsulmatuul | caius cosades | Balmora, Caius Cosades' House | 16 | unlocks *a2_6_incarnate* | — |
| 29 | a2_2_6thhouse | caius cosades | Balmora, Caius Cosades' House | 13 | unlocks *a2_6_incarnate* | — |
| 30 | a2_3_corpruscure | caius cosades | Balmora, Caius Cosades' House | 13 | unlocks *a2_6_incarnate* | `removespell` |
| 31 | a2_4_milogone | caius cosades | Balmora, Caius Cosades' House | 14 | unlocks *a2_6_incarnate* | — |
| 32 | a2_6_incarnate | nibani maesa | Urshilaku Camp, Wise Woman's Yurt | 24 | — | `getpcrank`, `modfactionreaction`, `rotate` |
| 33 | a1_10_mehramilo | mehra milo | Vivec, Library of Vivec | 5 | unlocks *a1_sleepersawake* | — |
| 34 | a1_6_addhiranirrinformant | adaves therayn | Vivec, St. Olms Waistworks | 9 | unlocks *a1_sleepersawake* | — |
| 35 | a1_dreams | script: Sleepers | ? | 4 | unlocks *a1_sleepersawake* | `getpcsleep` |
| 36 | a1_sleepersawake | Alvura Othrenim | Vivec, Arena Waistworks | 4 | — | — |

**224 further command(s) share a handler with one of these and are covered by testing it** -- `Enable` and `Disable` are one `OpSetEnabled`, every attribute and skill command one `OpStat`. The classes come from the runtime's own registrations, and `OpStat` is split by verb and by whether the stat maps to a Skyrim actor value, since those are genuinely different code paths.

| Tested | Also covers |
|---|---|
| `modaxe` | `modalchemy`, `modalteration`, `modarmorbonus`, `modarmorer`, `modblock`, `modbluntweapon`, `modconjuration`, `moddestruction`, `modenchant`, `modhandtohand`, `modheavyarmor`, `modillusion`, `modinvisible`, `modlightarmor`, `modlongblade`, `modmarksman`, `modmediumarmor`, `modmercantile`, `modmysticism`, `modparalysis`, `modresistdisease`, `modresistfire`, `modresistfrost`, `modresistmagicka`, `modresistpoison`, `modresistshock`, `modrestoration`, `modsecurity`, `modshortblade`, `modsneak`, `modspear`, `modspeechcraft`, `modunarmored`, `modwaterbreathing`, `modwaterwalking`, `setalchemy`, `setalteration`, `setarmorbonus`, `setarmorer`, `setaxe`, `setblock`, `setbluntweapon`, `setconjuration`, `setdestruction`, `setenchant`, `sethandtohand`, `setheavyarmor`, `setillusion`, `setinvisible`, `setlightarmor`, `setlongblade`, `setmarksman`, `setmediumarmor`, `setmercantile`, `setmysticism`, `setparalysis`, `setresistdisease`, `setresistfire`, `setresistfrost`, `setresistmagicka`, `setresistpoison`, `setresistshock`, `setrestoration`, `setsecurity`, `setshortblade`, `setsneak`, `setspear`, `setspeechcraft`, `setunarmored`, `setwaterbreathing`, `setwaterwalking` |
| `modagility` | `modacrobatics`, `modathletics`, `modattackbonus`, `modblindness`, `modcastpenalty`, `modchameleon`, `moddefendbonus`, `modendurance`, `modflying`, `modintelligence`, `modluck`, `modpersonality`, `modresistblight`, `modresistcorprus`, `modresistnormalweapons`, `modresistparalysis`, `modsilence`, `modspeed`, `modstrength`, `modsuperjump`, `modswimspeed`, `modwillpower`, `setacrobatics`, `setagility`, `setathletics`, `setattackbonus`, `setblindness`, `setcastpenalty`, `setchameleon`, `setdefendbonus`, `setendurance`, `setflying`, `setintelligence`, `setluck`, `setpersonality`, `setresistblight`, `setresistcorprus`, `setresistnormalweapons`, `setresistparalysis`, `setsilence`, `setspeed`, `setstrength`, `setsuperjump`, `setswimspeed`, `setwillpower` |
| `getalchemy` | `getalteration`, `getarmorbonus`, `getarmorer`, `getaxe`, `getblock`, `getbluntweapon`, `getconjuration`, `getdestruction`, `getenchant`, `gethandtohand`, `getheavyarmor`, `getillusion`, `getinvisible`, `getlightarmor`, `getlongblade`, `getmarksman`, `getmediumarmor`, `getmercantile`, `getmysticism`, `getparalysis`, `getresistdisease`, `getresistfire`, `getresistfrost`, `getresistmagicka`, `getresistpoison`, `getresistshock`, `getrestoration`, `getsecurity`, `getshortblade`, `getsneak`, `getspear`, `getspeechcraft`, `getunarmored`, `getwaterbreathing`, `getwaterwalking` |
| `getagility` | `getacrobatics`, `getathletics`, `getattackbonus`, `getblindness`, `getcastpenalty`, `getchameleon`, `getdefendbonus`, `getendurance`, `getflying`, `getintelligence`, `getluck`, `getpersonality`, `getresistblight`, `getresistcorprus`, `getresistnormalweapons`, `getresistparalysis`, `getsilence`, `getspeed`, `getstrength`, `getsuperjump`, `getswimspeed`, `getwillpower` |
| `disable` | `disableplayercontrols`, `disableplayerfighting`, `disableplayerlooking`, `disableplayerviewswitch`, `enable`, `enableplayercontrols`, `enableplayerfighting`, `enableplayerlooking`, `enableplayerviewswitch` |
| `modcurrenthealth` | `modcurrentfatigue`, `modcurrentmagicka`, `modfatigue`, `modhealth`, `modmagicka` |
| `getdisabled` | `getplayercontrolsdisabled`, `getplayerfightingdisabled`, `getplayerlookingdisabled`, `getplayerviewswitchdisabled` |
| `getdetected` | `getlineofsight`, `getlos`, `gettarget` |
| `getflee` | `getalarm`, `getfight`, `gethello` |
| `modfight` | `modalarm`, `modflee`, `modhello` |
| `setalarm` | `setfight`, `setflee`, `sethello` |
| `cellchanged` | `onactivate`, `ondeath` |
| `gethealth` | `getfatigue`, `getmagicka` |
| `sethealth` | `setfatigue`, `setmagicka` |
| `aiescort` | `aifollow` |
| `aifollowcell` | `aiescortcell` |
| `getpcrunning` | `getpcsneaking` |
| `getspellreadied` | `getweapondrawn` |
| `getstartingangle` | `getstartingpos` |
| `modpcfacrep` | `setpcfacrep` |
| `move` | `moveworld` |
| `pcclearexpelled` | `pcexpell` |
| `placeatpc` | `placeatme` |
| `playloopsound3dvp` | `playsound3dvp` |
| `playsound3d` | `playloopsound3d` |
| `ra` | `resetactors` |
| `raiserank` | `lowerrank` |
| `rotate` | `rotateworld` |
| `setpccrimelevel` | `modpccrimelevel` |

**Called by Morrowind.esm, but never from a quest** — these sit on ambient object scripts (doors, cranks, lights), so they need a hand-written probe rather than a quest:

`hassoulgem` (85), `removesoulgem` (42), `playsound` (34), `getsoundplaying` (31), `playloopsound3dvp` (24), `unlock` (14), `lock` (13), `modcurrenthealth` (11), `setangle` (10), `random` (8), `position` (8), `stopsound` (6), `forcesneak` (6), `drop` (6), `setatstart` (4), `getstartingangle` (4), `fall` (3), `getcurrentweather` (2), `scriptrunning` (1), `resurrect` (1), `playsoundvp` (1), `pcexpelled` (1), `getlocked` (1), `getdisposition` (1), `getangle` (1), `enableracemenu` (1)

**No call site anywhere in Morrowind.esm** (32) — untestable from it at all; they need a different plugin:

`addsoulgem`, `aiactivate`, `dropsoulgem`, `equip`, `explodespell`, `face`, `getalchemy`, `getcurrenttime`, `getfactionreaction`, `getforcesneak`, `getinterior`, `getlevel`, `getpcfacrep`, `getpcrunning`, `getreputation`, `getscale`, `getspellreadied`, `getsquareroot`, `hasitemequipped`, `modscale`, `pclowerrank`, `placeitem`, `placeitemcell`, `ra`, `removeeffects`, `removespelleffects`, `samefaction`, `setdelete`, `setfactionreaction`, `setjournalindex`, `setreputation`, `setscale`

## Stubbed commands — registered but doing nothing (13 of 32 covered)

Running these routes the player through commands that compile and dispatch but do nothing, so the log names the silent no-op behind each broken stage.

| # | Quest | Quest giver | `coc` target / location | Stages | Why | Commands it is first to test |
|--:|---|---|---|--:|---|---|
| 1 | c3_destroydagoth | dagoth_ur_1 | Dagoth Ur, Facility Cavern | 4 | — | `changeweather`, `enableteleporting`, `modregion`, `playbink`, `playgroup` |
| 2 | ht_minecure | aryon | Tel Vos, Aryon's Chambers | 4 | — | `getblightdisease` |
| 3 | tt_curingtouch | tharer rotheloth | Molag Mar, Temple | 4 | — | `getcommondisease` |
| 4 | hr_clearsarethi | athyn sarethi | Ald-ruhn, Sarethi Manor | 9 | unlocks *hr_honorchallenge* | — |
| 5 | hr_honorchallenge | athyn sarethi | Ald-ruhn, Sarethi Manor | 6 | — | `onknockout`, `onmurder` |
| 6 | a1_1_findspymaster | chargen captain | Seyda Neen, Census and Excise Office | 10 | unlocks *a1_sleepersawake* | `gotojail`, `payfine`, `payfinethief` |
| 7 | a1_2_antabolisinformant | caius cosades | Balmora, Caius Cosades' House | 7 | unlocks *a1_sleepersawake* | — |
| 8 | a1_4_muzgobinformant | caius cosades | Balmora, Caius Cosades' House | 8 | unlocks *a1_sleepersawake* | — |
| 9 | a1_v_vivecinformants | caius cosades | Balmora, Caius Cosades' House | 3 | unlocks *a1_sleepersawake* | — |
| 10 | a1_10_mehramilo | mehra milo | Vivec, Library of Vivec | 5 | unlocks *a1_sleepersawake* | — |
| 11 | a1_11_zainsubaniinformant | caius cosades | Balmora, Caius Cosades' House | 9 | unlocks *a1_sleepersawake* | — |
| 12 | a1_6_addhiranirrinformant | adaves therayn | Vivec, St. Olms Waistworks | 9 | unlocks *a1_sleepersawake* | — |
| 13 | a2_1_meetsulmatuul | caius cosades | Balmora, Caius Cosades' House | 16 | unlocks *a1_sleepersawake* | — |
| 14 | a2_2_6thhouse | caius cosades | Balmora, Caius Cosades' House | 13 | unlocks *a1_sleepersawake* | — |
| 15 | a1_dreams | script: Sleepers | ? | 4 | unlocks *a1_sleepersawake* | `wakeuppc` |
| 16 | a1_sleepersawake | Alvura Othrenim | Vivec, Arena Waistworks | 4 | — | — |

**Called by Morrowind.esm, but never from a quest** — these sit on ambient object scripts (doors, cranks, lights), so they need a hand-written probe rather than a quest:

`xbox` (13), `getstandingpc` (9), `enablestatsmenu` (3), `enablemapmenu` (3), `enablemagicmenu` (3), `enableinventorymenu` (3), `showrestmenu` (2), `hitonme` (2), `streammusic` (1), `loopgroup` (1), `hurtstandingactor` (1), `getattacked` (1), `enablestatreviewmenu` (1), `enablerest` (1), `enablenamemenu` (1), `enableclassmenu` (1), `enablebirthmenu` (1), `disableteleporting` (1), `disableplayerjumping` (1)

