# MWScript opcodes in MorrowindRuntime

**Tool:** `python tools/script/mwscript_opcode_audit.py --export "export/Tamriel Rebuilt 25.08.12" --markdown <this file>`

Measured over `export/Tamriel Rebuilt 25.08.12`: 298 registered command(s), 90395 call site(s) across BOTH corpora -- the INFO result scripts in `MWIN.txt` and the object scripts in `SCPT.txt`.

| Status | Commands | Call sites |
|---|---:|---:|
| ported | 54 | 67291 |
| no-op | 5 | 2615 |
| STUB | 239 | 20489 |

🛑 **102 of the 239 stubbed commands have ZERO call sites in either corpus** — OpenMW's console (`tgm`, `coc`, every `toggle*`), the chargen menu toggles, the Bloodmoon werewolf commands and OpenMW's own hooks (`reloadlua`, `setnavmeshnumber`). The real remaining work is the 137 command(s) below.

## Stubbed, and something calls it

| Command | Domain | Signature | Calls |
|---|---|---|---:|
| `positioncell` | Transformation | `ffffczz` | 1232 |
| `aiwander` | Ai | `fff/llllllllll` | 1207 |
| `menumode` | Misc | — → `l` | 1111 |
| `getsecondspassed` | Misc | — → `f` | 1005 |
| `cellchanged` | Cell | — → `l` | 983 |
| `ondeath` | Stats | — → `l` | 958 |
| `getpos` | Transformation | `c` → `f` | 911 |
| `onactivate` | Misc | — → `l` | 852 |
| `playsound` | Sound | `cXX` | 804 |
| `getdistance` | Transformation | `c` → `f` | 782 |
| `setpos` | Transformation | `cf` | 670 |
| `forcegreeting` | Dialogue | `z` | 579 |
| `addspell` | Stats | `cz` | 570 |
| `activate` | Misc | `x` | 504 |
| `help` | Misc | — | 398 |
| `moveworld` | Transformation | `cf` | 363 |
| `getpccell` | Cell | `c` → `l` | 350 |
| `unlock` | Misc | — | 332 |
| `cast` | Misc | `SS` | 310 |
| `aitravel` | Ai | `fff/lx` | 292 |
| `say` | Sound | `SS` | 292 |
| `playsound3d` | Sound | `cXX` | 280 |
| `aifollow` | Ai | `cffff/llllllll` | 266 |
| `placeatme` | Transformation | `clflX` | 241 |
| `getrace` | Stats | `c` → `l` | 238 |
| `getspell` | Stats | `c` → `l` | 207 |
| `rotate` | Transformation | `cf` | 206 |
| `lock` | Misc | `/l` | 202 |
| `removespell` | Stats | `cz` | 201 |
| `placeitemcell` | Transformation | `ccffffX` | 179 |
| `playgroup` | Animation | `c/l` | 178 |
| `setangle` | Transformation | `cf` | 159 |
| `getaipackagedone` | Ai | — → `l` | 150 |
| `move` | Transformation | `cf` | 136 |
| `setdelete` | Misc | `l` | 133 |
| `getbuttonpressed` | Gui | — → `l` | 131 |
| `placeatpc` | Transformation | `clflX` | 128 |
| `equip` | Container | `cX` | 127 |
| `hassoulgem` | Container | `c` → `l` | 123 |
| `geteffect` | Misc | `S` → `l` | 121 |
| `getsoundplaying` | Sound | `c` → `l` | 120 |
| `drop` | Misc | `cl` | 115 |
| `explodespell` | Misc | `S` | 106 |
| `playsoundvp` | Sound | `cff` | 97 |
| `rotateworld` | Transformation | `cf` | 92 |
| `playsound3dvp` | Sound | `cff` | 85 |
| `getspelleffects` | Misc | `c` → `l` | 82 |
| `getcurrentaipackage` | Ai | — → `l` | 75 |
| `getlocked` | Misc | — → `l` | 74 |
| `face` | Ai | `ffX` | 73 |
| `stopsound` | Sound | `cXX` | 72 |
| `getangle` | Transformation | `c` → `f` | 69 |
| `removesoulgem` | Misc | `c/l` | 64 |
| `playloopsound3dvp` | Sound | `cff` | 59 |
| `getcurrentweather` | Sky | — → `l` | 55 |
| `removeeffects` | Stats | `l` | 54 |
| `getinterior` | Cell | — → `l` | 53 |
| `getlos` | Ai | `c` → `l` | 50 |
| `gettarget` | Ai | `c` → `l` | 49 |
| `getattacked` | Misc | — → `l` | 45 |
| `fall` | Misc | — | 44 |
| `saydone` | Sound | — → `l` | 44 |
| `setscale` | Transformation | `f` | 42 |
| `show` | Misc | `c` | 41 |
| `position` | Transformation | `ffffz` | 40 |
| `getdetected` | Ai | `c` → `l` | 39 |
| `getweapondrawn` | Misc | — → `l` | 38 |
| `forcerun` | Control | — | 36 |
| `getpcsneaking` | Control | — → `l` | 31 |
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
| `gotojail` | Misc | — | 19 |
| `playloopsound3d` | Sound | `cXX` | 19 |
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
| `getstandingactor` | Misc | — → `l` | 14 |
| `loopgroup` | Animation | `cl/l` | 13 |
| `removefromlevcreature` | Misc | `ccl` | 13 |
| `disableteleporting` | Misc | — | 12 |
| `setwaterlevel` | Cell | `f` | 12 |
| `forcesneak` | Control | — | 11 |
| `getpcjumping` | Misc | — → `l` | 11 |
| `getstandingpc` | Misc | — → `l` | 10 |
| `hurtcollidingactor` | Misc | `f` | 10 |
| `aifollowcell` | Ai | `ccffff/l` | 9 |
| `getcollidingpc` | Misc | — → `l` | 9 |
| `removespelleffects` | Stats | `c` | 9 |
| `showrestmenu` | Gui | — | 9 |
| `wakeuppc` | Misc | — | 9 |
| `aiactivate` | Ai | `c/l` | 6 |
| `getpcrunning` | Control | — → `l` | 6 |
| `resetactors` | Transformation | — | 6 |
| `dontsaveobject` | Misc | — | 5 |
| `getforcesneak` | Control | — → `l` | 5 |
| `hurtstandingactor` | Misc | `f` | 5 |
| `getcurrenttime` | Misc | — → `f` | 4 |
| `getforcerun` | Control | — → `l` | 4 |
| `getweapontype` | Container | — → `l` | 4 |
| `modscale` | Transformation | `f` | 4 |
| `payfine` | Misc | — | 4 |
| `togglemenus` | Gui | — | 4 |
| `forcejump` | Control | — | 3 |
| `forcemovejump` | Control | — | 3 |
| `getblightdisease` | Stats | — → `l` | 3 |
| `getstartingangle` | Transformation | `c` → `f` | 3 |
| `menutest` | Gui | `/l` | 3 |
| `onknockout` | Stats | — → `l` | 3 |
| `onmurder` | Stats | — → `l` | 3 |
| `skipanim` | Animation | — | 3 |
| `clearforcejump` | Control | — | 2 |
| `clearforcemovejump` | Control | — | 2 |
| `lowerrank` | Stats | `x` | 2 |
| `aiescortcell` | Ai | `ccffff/l` | 1 |
| `disablelevitation` | Misc | — | 1 |
| `enablelevitation` | Misc | — | 1 |
| `getforcemovejump` | Control | — → `l` | 1 |
| `hitonme` | Misc | `S` → `l` | 1 |
| `streammusic` | Sound | `S` | 1 |

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
| `startscript` | Misc | `c` | 1104 |
| `getdeadcount` | Stats | `c` → `l` | 1074 |
| `stopscript` | Misc | `c` | 695 |
| `stopcombat` | Ai | `x` | 276 |
| `modreputation` | Dialogue | `l` | 262 |
| `sethello` | Ai | `l` | 235 |
| `random` | Misc | `l` → `f` | 215 |
| `setdisposition` | Stats | `l` | 159 |
| `setalarm` | Ai | `l` | 133 |
| `pcexpell` | Stats | `/S` | 119 |
| `modpccrimelevel` | Stats | `f` | 114 |
| `getpcrank` | Stats | `/S` → `l` | 110 |
| `modfight` | Ai | `l` | 109 |
| `getpccrimelevel` | Stats | — → `f` | 91 |
| `getdisposition` | Stats | — → `l` | 90 |
| `scriptrunning` | Misc | `c` → `l` | 73 |
| `setjournalindex` | Dialogue | `cl` | 59 |
| `setflee` | Ai | `l` | 50 |
| `pcraiserank` | Stats | `/S` | 39 |
| `setpccrimelevel` | Stats | `f` | 37 |
| `getfight` | Ai | — → `l` | 22 |
| `pcjoinfaction` | Stats | `/S` | 12 |
| `modfactionreaction` | Dialogue | `ccl` | 11 |
| `gethello` | Ai | — → `l` | 9 |
| `pcclearexpelled` | Stats | `/S` | 9 |
| `modflee` | Ai | `l` | 5 |
| `pcexpelled` | Stats | `/S` → `l` | 5 |
| `getalarm` | Ai | — → `l` | 4 |
| `pclowerrank` | Stats | `/S` | 4 |
| `samefaction` | Dialogue | — → `l` | 4 |
| `setpcfacrep` | Stats | `l/c` | 3 |
| `modalarm` | Ai | `l` | 2 |
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

`addtolevcreature`, `addtolevitem`, `bc`, `becomewerewolf`, `betacomment`, `centeroncell`, `centeronexterior`, `coc`, `coe`, `dropsoulgem`, `enablebirthmenu`, `enableclassmenu`, `enableinventorymenu`, `enablelevelupmenu`, `enablemagicmenu`, `enablemapmenu`, `enablenamemenu`, `enableracemenu`, `enablerest`, `enablestatreviewmenu`, `enablestatsmenu`, `filljournal`, `fillmap`, `fixme`, `getcollidingactor`, `getforcejump`, `getlineofsight`, `getmasserphase`, `getpcinjail`, `getpctraveling`, `getpcvisionbonus`, `getsecundaphase`, `getstartingpos`, `getstat`, `getwerewolfkills`, `hitattemptonme`, `iswerewolf`, `modpcvisionbonus`, `modregion`, `ori`, `outputrefinfo`, `pcforce1stperson`, `pcforce3rdperson`, `pcget3rdperson`, `playbink`, `reloadlua`, `removefromlevitem`, `repairedonme`, `setlevel`, `setnavmeshnumber`, `setpcvisionbonus`, `setwerewolfacrobatics`, `showscenegraph`, `showvars`, `ssg`, `sv`, `t3d`, `tai`, `tap`, `tb`, `tcb`, `tcg`, `tcl`, `testcells`, `testinteriorcells`, `testmodels`, `tfh`, `tfow`, `tgm`, `tm`, `toggleactorspaths`, `toggleai`, `toggleborders`, `togglecollision`, `togglecollisionboxes`, `togglecollisiongrid`, `togglefogofwar`, `togglefullhelp`, `togglegodmode`, `togglenavmesh`, `togglepathgrid`, `togglerecastmesh`, `togglescripts`, `togglesky`, `togglevanitymode`, `togglewater`, `togglewireframe`, `toggleworld`, `tpg`, `ts`, `turnmoonred`, `turnmoonwhite`, `tvm`, `tw`, `twa`, `twf`, `undowerewolf`, `user1`, `user2`, `user3`, `user4`, `xbox`
