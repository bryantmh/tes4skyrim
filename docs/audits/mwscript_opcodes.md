# MWScript opcodes in MorrowindRuntime

**Tool:** `python tools/script/mwscript_opcode_audit.py --export "export/Tamriel Rebuilt 25.08.12" --markdown <this file>`

Measured over `export/Tamriel Rebuilt 25.08.12`: 487 registered command(s), 91581 call site(s) across BOTH corpora -- the INFO result scripts in `MWIN.txt` and the object scripts in `SCPT.txt`.

| Status | Commands | Call sites |
|---|---:|---:|
| ported | 95 | 79784 |
| no-op | 5 | 2615 |
| STUB | 387 | 9182 |

🛑 **207 of the 387 stubbed commands have ZERO call sites in either corpus** — OpenMW's console (`tgm`, `coc`, every `toggle*`), the chargen menu toggles, the Bloodmoon werewolf commands and OpenMW's own hooks (`reloadlua`, `setnavmeshnumber`). The real remaining work is the 180 command(s) below.

## Stubbed, and something calls it

| Command | Domain | Signature | Calls |
|---|---|---|---:|
| `positioncell` | Transformation | `ffffczz` | 1232 |
| `aiwander` | Ai | `fff/llllllllll` | 1207 |
| `addspell` | Stats | `cz` | 570 |
| `help` | Misc | — | 398 |
| `moveworld` | Transformation | `cf` | 363 |
| `cast` | Misc | `SS` | 310 |
| `aitravel` | Ai | `fff/lx` | 292 |
| `say` | Sound | `SS` | 292 |
| `aifollow` | Ai | `cffff/llllllll` | 266 |
| `placeatme` | Transformation | `clflX` | 241 |
| `getspell` | Stats | `c` → `l` | 207 |
| `rotate` | Transformation | `cf` | 206 |
| `removespell` | Stats | `cz` | 201 |
| `placeitemcell` | Transformation | `ccffffX` | 179 |
| `playgroup` | Animation | `c/l` | 178 |
| `getaipackagedone` | Ai | — → `l` | 150 |
| `move` | Transformation | `cf` | 136 |
| `getbuttonpressed` | Gui | — → `l` | 131 |
| `hassoulgem` | Container | `c` → `l` | 123 |
| `geteffect` | Misc | `S` → `l` | 121 |
| `drop` | Misc | `cl` | 115 |
| `explodespell` | Misc | `S` | 106 |
| `rotateworld` | Transformation | `cf` | 92 |
| `getspelleffects` | Misc | `c` → `l` | 82 |
| `getcurrentaipackage` | Ai | — → `l` | 75 |
| `face` | Ai | `ffX` | 73 |
| `removesoulgem` | Misc | `c/l` | 64 |
| `getcurrentweather` | Sky | — → `l` | 55 |
| `removeeffects` | Stats | `l` | 54 |
| `getlos` | Ai | `c` → `l` | 50 |
| `gettarget` | Ai | `c` → `l` | 49 |
| `getattacked` | Misc | — → `l` | 45 |
| `fall` | Misc | — | 44 |
| `saydone` | Sound | — → `l` | 44 |
| `modrestoration` | Stats | `f` | 43 |
| `setscale` | Transformation | `f` | 42 |
| `show` | Misc | `c` | 41 |
| `getspeechcraft` | Stats | — → `f` | 40 |
| `position` | Transformation | `ffffz` | 40 |
| `getdetected` | Ai | `c` → `l` | 39 |
| `getweapondrawn` | Misc | — → `l` | 38 |
| `forcerun` | Control | — | 36 |
| `modmercantile` | Stats | `f` | 33 |
| `getpcsneaking` | Control | — → `l` | 31 |
| `setparalysis` | Stats | `l` | 31 |
| `getintelligence` | Stats | — → `f` | 30 |
| `addsoulgem` | Misc | `ccX` | 28 |
| `aiescort` | Ai | `cffff/l` | 28 |
| `getpcsleep` | Misc | — → `l` | 26 |
| `clearforcesneak` | Control | — | 25 |
| `modwaterlevel` | Cell | `f` | 22 |
| `ra` | Transformation | — | 22 |
| `enableteleporting` | Misc | — | 21 |
| `getarmortype` | Container | `l` → `l` | 21 |
| `getwindspeed` | Misc | — → `f` | 21 |
| `hasitemequipped` | Container | `c` → `l` | 20 |
| `getstrength` | Stats | — → `f` | 19 |
| `gotojail` | Misc | — | 19 |
| `resurrect` | Stats | — | 19 |
| `setatstart` | Transformation | — | 19 |
| `getcommondisease` | Stats | — → `l` | 18 |
| `getspellreadied` | Misc | — → `l` | 18 |
| `payfinethief` | Misc | — | 18 |
| `placeitem` | Transformation | `cffffX` | 18 |
| `clearforcerun` | Control | — | 17 |
| `getsquareroot` | Misc | `f` → `f` | 16 |
| `changeweather` | Sky | `Sl` | 15 |
| `getlevel` | Stats | — → `l` | 15 |
| `getscale` | Transformation | — → `f` | 15 |
| `getwaterlevel` | Cell | — → `f` | 15 |
| `raiserank` | Stats | `x` | 15 |
| `setspeed` | Stats | `f` | 15 |
| `getstandingactor` | Misc | — → `l` | 14 |
| `getluck` | Stats | — → `f` | 13 |
| `loopgroup` | Animation | `cl/l` | 13 |
| `removefromlevcreature` | Misc | `ccl` | 13 |
| `disableteleporting` | Misc | — | 12 |
| `modalteration` | Stats | `f` | 12 |
| `setwaterlevel` | Cell | `f` | 12 |
| `forcesneak` | Control | — | 11 |
| `getinvisible` | Stats | — → `l` | 11 |
| `getmercantile` | Stats | — → `f` | 11 |
| `getparalysis` | Stats | — → `l` | 11 |
| `getpcjumping` | Misc | — → `l` | 11 |
| `getstandingpc` | Misc | — → `l` | 10 |
| `hurtcollidingactor` | Misc | `f` | 10 |
| `aifollowcell` | Ai | `ccffff/l` | 9 |
| `getcollidingpc` | Misc | — → `l` | 9 |
| `getsecurity` | Stats | — → `f` | 9 |
| `removespelleffects` | Stats | `c` | 9 |
| `showrestmenu` | Gui | — | 9 |
| `wakeuppc` | Misc | — | 9 |
| `sethandtohand` | Stats | `f` | 8 |
| `setmarksman` | Stats | `f` | 8 |
| `setmercantile` | Stats | `f` | 8 |
| `setstrength` | Stats | `f` | 7 |
| `aiactivate` | Ai | `c/l` | 6 |
| `getalchemy` | Stats | — → `f` | 6 |
| `getchameleon` | Stats | — → `l` | 6 |
| `getpcrunning` | Control | — → `l` | 6 |
| `getwillpower` | Stats | — → `f` | 6 |
| `modalchemy` | Stats | `f` | 6 |
| `resetactors` | Transformation | — | 6 |
| `setchameleon` | Stats | `l` | 6 |
| `dontsaveobject` | Misc | — | 5 |
| `getforcesneak` | Control | — → `l` | 5 |
| `getillusion` | Stats | — → `f` | 5 |
| `hurtstandingactor` | Misc | `f` | 5 |
| `modacrobatics` | Stats | `f` | 5 |
| `setshortblade` | Stats | `f` | 5 |
| `getconjuration` | Stats | — → `f` | 4 |
| `getcurrenttime` | Misc | — → `f` | 4 |
| `getforcerun` | Control | — → `l` | 4 |
| `getpersonality` | Stats | — → `f` | 4 |
| `getweapontype` | Container | — → `l` | 4 |
| `modscale` | Transformation | `f` | 4 |
| `payfine` | Misc | — | 4 |
| `setathletics` | Stats | `f` | 4 |
| `setaxe` | Stats | `f` | 4 |
| `setlongblade` | Stats | `f` | 4 |
| `togglemenus` | Gui | — | 4 |
| `forcejump` | Control | — | 3 |
| `forcemovejump` | Control | — | 3 |
| `getalteration` | Stats | — → `f` | 3 |
| `getblightdisease` | Stats | — → `l` | 3 |
| `getendurance` | Stats | — → `f` | 3 |
| `getsneak` | Stats | — → `f` | 3 |
| `getspear` | Stats | — → `f` | 3 |
| `getspeed` | Stats | — → `f` | 3 |
| `getstartingangle` | Transformation | `c` → `f` | 3 |
| `menutest` | Gui | `/l` | 3 |
| `modshortblade` | Stats | `f` | 3 |
| `onknockout` | Stats | — → `l` | 3 |
| `onmurder` | Stats | — → `l` | 3 |
| `setacrobatics` | Stats | `f` | 3 |
| `setagility` | Stats | `f` | 3 |
| `setbluntweapon` | Stats | `f` | 3 |
| `setmediumarmor` | Stats | `f` | 3 |
| `skipanim` | Animation | — | 3 |
| `clearforcejump` | Control | — | 2 |
| `clearforcemovejump` | Control | — | 2 |
| `getdestruction` | Stats | — → `f` | 2 |
| `gethandtohand` | Stats | — → `f` | 2 |
| `getlongblade` | Stats | — → `f` | 2 |
| `getmysticism` | Stats | — → `f` | 2 |
| `lowerrank` | Stats | `x` | 2 |
| `modagility` | Stats | `f` | 2 |
| `modaxe` | Stats | `f` | 2 |
| `moddestruction` | Stats | `f` | 2 |
| `modenchant` | Stats | `f` | 2 |
| `modlongblade` | Stats | `f` | 2 |
| `modspear` | Stats | `f` | 2 |
| `modspeechcraft` | Stats | `f` | 2 |
| `setblock` | Stats | `f` | 2 |
| `setwaterbreathing` | Stats | `l` | 2 |
| `aiescortcell` | Ai | `ccffff/l` | 1 |
| `disablelevitation` | Misc | — | 1 |
| `enablelevitation` | Misc | — | 1 |
| `getagility` | Stats | — → `f` | 1 |
| `getarmorer` | Stats | — → `f` | 1 |
| `getforcemovejump` | Control | — → `l` | 1 |
| `getresistmagicka` | Stats | — → `l` | 1 |
| `getrestoration` | Stats | — → `f` | 1 |
| `hitonme` | Misc | `S` → `l` | 1 |
| `modbluntweapon` | Stats | `f` | 1 |
| `modchameleon` | Stats | `l` | 1 |
| `modconjuration` | Stats | `f` | 1 |
| `modmarksman` | Stats | `f` | 1 |
| `modmediumarmor` | Stats | `f` | 1 |
| `modmysticism` | Stats | `f` | 1 |
| `modpersonality` | Stats | `f` | 1 |
| `modstrength` | Stats | `f` | 1 |
| `modunarmored` | Stats | `f` | 1 |
| `setarmorbonus` | Stats | `l` | 1 |
| `setdestruction` | Stats | `f` | 1 |
| `setendurance` | Stats | `f` | 1 |
| `setluck` | Stats | `f` | 1 |
| `setsneak` | Stats | `f` | 1 |
| `setwillpower` | Stats | `f` | 1 |
| `streammusic` | Sound | `S` | 1 |

## Blocked on EXPORT or IMPORT, not on the runtime

Porting the opcode alone cannot fix these: the data it would name is not converted yet.

| Calls | Needs | Commands | Why |
|---:|---|---|---|
| 1943 | PACK | `aiwander` `aitravel` `aifollow` `aiescort` `getaipackagedone` | `PACK.txt` IS exported but no package is staged, and Skyrim needs a real PACK record rather than a runtime call |
| 1420 | CELL | `positioncell` `placeitemcell` `aifollowcell` `getpccell` | `CELL.txt` IS exported but no cell id -> FormID table is staged |
| 978 | SPEL | `addspell` `removespell` `getspell` `hasspell` | no `SPEL.txt` is exported, so a spell id resolves to nothing |
| 673 | MGEF/ENCH | `cast` `explodespell` `getspelleffects` `geteffect` `removeeffects` | no `MGEF.txt` or `ENCH.txt`; an effect has no FormID to name |
| 215 | SLGM | `addsoulgem` `removesoulgem` `hassoulgem` `dropsoulgem` | TES3 soul gems export as MISC, so they are clutter in Skyrim |

🛑 **A TES3 soul gem carries NO soul field.** OpenMW decides by id prefix -- `mwclass/misc.cpp:isSoulGem` is `getRefId().startsWith("misc_soulgem")` -- and the trapped soul lives on the CellRef, not the base record. Measured over TR_Mainland plus the Morroblivion patch: 6 MISC records match that prefix and 3 more merely contain "soulgem", so those 3 are NOT soul gems in Morrowind either. Converting them to SLGM means matching the prefix, never the name.

## Ported

| Command | Domain | Signature | Calls |
|---|---|---|---:|
| `journal` | Dialogue | `cl` | 11281 |
| `choice` | Dialogue | `j/SlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSl` | 8549 |
| `moddisposition` | Stats | `l` | 5711 |
| `getjournalindex` | Dialogue | `c` → `l` | 5665 |
| `additem` | Container | `clX` | 5248 |
| `addtopic` | Dialogue | `S` | 5247 |
| `goodbye` | Dialogue | — | 4841 |
| `removeitem` | Container | `clX` | 3075 |
| `disable` | Misc | `x` | 2910 |
| `getitemcount` | Container | `cX` → `l` | 1866 |
| `getdisabled` | Misc | `x` → `l` | 1856 |
| `startcombat` | Ai | `c` | 1764 |
| `setfight` | Ai | `l` | 1759 |
| `enable` | Misc | `x` | 1265 |
| `modpcfacrep` | Stats | `l/c` | 1118 |
| `menumode` | Misc | — → `l` | 1111 |
| `startscript` | Misc | `c` | 1104 |
| `getdeadcount` | Stats | `c` → `l` | 1074 |
| `getsecondspassed` | Misc | — → `f` | 1005 |
| `cellchanged` | Cell | — → `l` | 983 |
| `ondeath` | Stats | — → `l` | 958 |
| `getpos` | Transformation | `c` → `f` | 911 |
| `onactivate` | Misc | — → `l` | 852 |
| `playsound` | Sound | `cXX` | 804 |
| `getdistance` | Transformation | `c` → `f` | 782 |
| `stopscript` | Misc | `c` | 695 |
| `setpos` | Transformation | `cf` | 670 |
| `forcegreeting` | Dialogue | `z` | 579 |
| `activate` | Misc | `x` | 504 |
| `getpccell` | Cell | `c` → `l` | 350 |
| `unlock` | Misc | — | 332 |
| `playsound3d` | Sound | `cXX` | 280 |
| `stopcombat` | Ai | `x` | 276 |
| `modreputation` | Dialogue | `l` | 262 |
| `sethealth` | Stats | `f` | 245 |
| `getrace` | Stats | `c` → `l` | 238 |
| `sethello` | Ai | `l` | 235 |
| `random` | Misc | `l` → `f` | 215 |
| `gethealth` | Stats | `x` → `f` | 211 |
| `lock` | Misc | `/l` | 202 |
| `setangle` | Transformation | `cf` | 159 |
| `setdisposition` | Stats | `l` | 159 |
| `setalarm` | Ai | `l` | 133 |
| `setdelete` | Misc | `l` | 133 |
| `placeatpc` | Transformation | `clflX` | 128 |
| `equip` | Container | `cX` | 127 |
| `getsoundplaying` | Sound | `c` → `l` | 120 |
| `pcexpell` | Stats | `/S` | 119 |
| `modpccrimelevel` | Stats | `f` | 114 |
| `getpcrank` | Stats | `/S` → `l` | 110 |
| `modfight` | Ai | `l` | 109 |
| `playsoundvp` | Sound | `cff` | 97 |
| `getpccrimelevel` | Stats | — → `f` | 91 |
| `getdisposition` | Stats | — → `l` | 90 |
| `playsound3dvp` | Sound | `cff` | 85 |
| `getlocked` | Misc | — → `l` | 74 |
| `scriptrunning` | Misc | `c` → `l` | 73 |
| `stopsound` | Sound | `cXX` | 72 |
| `getangle` | Transformation | `c` → `f` | 69 |
| `playloopsound3dvp` | Sound | `cff` | 59 |
| `setjournalindex` | Dialogue | `cl` | 59 |
| `setfatigue` | Stats | `f` | 58 |
| `getinterior` | Cell | — → `l` | 53 |
| `modcurrentfatigue` | Stats | `f` | 51 |
| `setflee` | Ai | `l` | 50 |
| `modcurrenthealth` | Stats | `f` | 48 |
| `getfatigue` | Stats | `x` → `f` | 43 |
| `pcraiserank` | Stats | `/S` | 39 |
| `setpccrimelevel` | Stats | `f` | 37 |
| `setmagicka` | Stats | `f` | 33 |
| `getfight` | Ai | — → `l` | 22 |
| `playloopsound3d` | Sound | `cXX` | 19 |
| `modfatigue` | Stats | `f` | 14 |
| `getmagicka` | Stats | `x` → `f` | 12 |
| `pcjoinfaction` | Stats | `/S` | 12 |
| `modfactionreaction` | Dialogue | `ccl` | 11 |
| `modcurrentmagicka` | Stats | `f` | 10 |
| `modhealth` | Stats | `f` | 10 |
| `gethello` | Ai | — → `l` | 9 |
| `pcclearexpelled` | Stats | `/S` | 9 |
| `modflee` | Ai | `l` | 5 |
| `pcexpelled` | Stats | `/S` → `l` | 5 |
| `getalarm` | Ai | — → `l` | 4 |
| `pclowerrank` | Stats | `/S` | 4 |
| `samefaction` | Dialogue | — → `l` | 4 |
| `setpcfacrep` | Stats | `l/c` | 3 |
| `modalarm` | Ai | `l` | 2 |
| `modmagicka` | Stats | `f` | 2 |
| `getflee` | Ai | — → `l` | 1 |
| `getreputation` | Dialogue | — → `l` | 1 |
| `getfactionreaction` | Dialogue | `ccX` → `l` |  |
| `getpcfacrep` | Stats | `/c` → `l` |  |
| `modhello` | Ai | `l` |  |
| `setfactionreaction` | Dialogue | `ccl` |  |
| `setreputation` | Dialogue | `l` |  |

## Deliberate no-ops

Nothing to port: Morrowind's own presentation, or state this runtime does not keep.

| Command | Domain | Signature | Calls |
|---|---|---|---:|
| `showmap` | Gui | `Sxxxx` | 1741 |
| `clearinfoactor` | Dialogue | — | 628 |
| `fadeout` | Misc | `f` | 126 |
| `fadein` | Misc | `f` | 120 |
| `fadeto` | Misc | `ff` |  |

## Stubbed, but nothing calls it

Console, debug and chargen commands. Listed for completeness; none is reachable from dialogue.

`addtolevcreature`, `addtolevitem`, `bc`, `becomewerewolf`, `betacomment`, `centeroncell`, `centeronexterior`, `coc`, `coe`, `dropsoulgem`, `enablebirthmenu`, `enableclassmenu`, `enableinventorymenu`, `enablelevelupmenu`, `enablemagicmenu`, `enablemapmenu`, `enablenamemenu`, `enableracemenu`, `enablerest`, `enablestatreviewmenu`, `enablestatsmenu`, `filljournal`, `fillmap`, `fixme`, `getacrobatics`, `getarmorbonus`, `getathletics`, `getattackbonus`, `getaxe`, `getblindness`, `getblock`, `getbluntweapon`, `getcastpenalty`, `getcollidingactor`, `getdefendbonus`, `getenchant`, `getflying`, `getforcejump`, `getheavyarmor`, `getlightarmor`, `getlineofsight`, `getmarksman`, `getmasserphase`, `getmediumarmor`, `getpcinjail`, `getpctraveling`, `getpcvisionbonus`, `getresistblight`, `getresistcorprus`, `getresistdisease`, `getresistfire`, `getresistfrost`, `getresistnormalweapons`, `getresistparalysis`, `getresistpoison`, `getresistshock`, `getsecundaphase`, `getshortblade`, `getsilence`, `getstartingpos`, `getstat`, `getsuperjump`, `getswimspeed`, `getunarmored`, `getwaterbreathing`, `getwaterwalking`, `getwerewolfkills`, `hitattemptonme`, `iswerewolf`, `modarmorbonus`, `modarmorer`, `modathletics`, `modattackbonus`, `modblindness`, `modblock`, `modcastpenalty`, `moddefendbonus`, `modendurance`, `modflying`, `modhandtohand`, `modheavyarmor`, `modillusion`, `modintelligence`, `modinvisible`, `modlightarmor`, `modluck`, `modparalysis`, `modpcvisionbonus`, `modregion`, `modresistblight`, `modresistcorprus`, `modresistdisease`, `modresistfire`, `modresistfrost`, `modresistmagicka`, `modresistnormalweapons`, `modresistparalysis`, `modresistpoison`, `modresistshock`, `modsecurity`, `modsilence`, `modsneak`, `modspeed`, `modsuperjump`, `modswimspeed`, `modwaterbreathing`, `modwaterwalking`, `modwillpower`, `ori`, `outputrefinfo`, `pcforce1stperson`, `pcforce3rdperson`, `pcget3rdperson`, `playbink`, `reloadlua`, `removefromlevitem`, `repairedonme`, `setalchemy`, `setalteration`, `setarmorer`, `setattackbonus`, `setblindness`, `setcastpenalty`, `setconjuration`, `setdefendbonus`, `setenchant`, `setflying`, `setheavyarmor`, `setillusion`, `setintelligence`, `setinvisible`, `setlevel`, `setlightarmor`, `setmysticism`, `setnavmeshnumber`, `setpcvisionbonus`, `setpersonality`, `setresistblight`, `setresistcorprus`, `setresistdisease`, `setresistfire`, `setresistfrost`, `setresistmagicka`, `setresistnormalweapons`, `setresistparalysis`, `setresistpoison`, `setresistshock`, `setrestoration`, `setsecurity`, `setsilence`, `setspear`, `setspeechcraft`, `setsuperjump`, `setswimspeed`, `setunarmored`, `setwaterwalking`, `setwerewolfacrobatics`, `showscenegraph`, `showvars`, `ssg`, `sv`, `t3d`, `tai`, `tap`, `tb`, `tcb`, `tcg`, `tcl`, `testcells`, `testinteriorcells`, `testmodels`, `tfh`, `tfow`, `tgm`, `tm`, `toggleactorspaths`, `toggleai`, `toggleborders`, `togglecollision`, `togglecollisionboxes`, `togglecollisiongrid`, `togglefogofwar`, `togglefullhelp`, `togglegodmode`, `togglenavmesh`, `togglepathgrid`, `togglerecastmesh`, `togglescripts`, `togglesky`, `togglevanitymode`, `togglewater`, `togglewireframe`, `toggleworld`, `tpg`, `ts`, `turnmoonred`, `turnmoonwhite`, `tvm`, `tw`, `twa`, `twf`, `undowerewolf`, `user1`, `user2`, `user3`, `user4`, `xbox`
