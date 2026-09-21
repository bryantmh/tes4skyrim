# MWScript opcodes in MorrowindRuntime

**Tool:** `python tools/script/mwscript_opcode_audit.py --export "export/Tamriel Rebuilt 25.08.12" --markdown <this file>`

Measured over `export/Tamriel Rebuilt 25.08.12`: 487 registered command(s), 90492 call site(s) across BOTH corpora -- the INFO result scripts in `MWIN.txt` and the object scripts in `SCPT.txt`.

| Status | Commands | Call sites |
|---|---:|---:|
| ported | 337 | 87253 |
| no-op | 16 | 2688 |
| STUB | 134 | 551 |

🛑 **99 of the 134 stubbed commands have ZERO call sites in either corpus** — OpenMW's console (`tgm`, `coc`, every `toggle*`), the chargen menu toggles, the Bloodmoon werewolf commands and OpenMW's own hooks (`reloadlua`, `setnavmeshnumber`). The real remaining work is the 35 command(s) below.

## Stubbed, and something calls it

| Command | Domain | Signature | Calls |
|---|---|---|---:|
| `playgroup` | Animation | `c/l` | 178 |
| `getattacked` | Misc | — → `l` | 45 |
| `modwaterlevel` | Cell | `f` | 22 |
| `enableteleporting` | Misc | — | 21 |
| `getarmortype` | Container | `l` → `l` | 21 |
| `getwindspeed` | Misc | — → `f` | 21 |
| `gotojail` | Misc | — | 19 |
| `getcommondisease` | Stats | — → `l` | 18 |
| `payfinethief` | Misc | — | 18 |
| `changeweather` | Sky | `Sl` | 15 |
| `getwaterlevel` | Cell | — → `f` | 15 |
| `getstandingactor` | Misc | — → `l` | 14 |
| `loopgroup` | Animation | `cl/l` | 13 |
| `removefromlevcreature` | Misc | `ccl` | 13 |
| `disableteleporting` | Misc | — | 12 |
| `setwaterlevel` | Cell | `f` | 12 |
| `getpcjumping` | Misc | — → `l` | 11 |
| `getstandingpc` | Misc | — → `l` | 10 |
| `hurtcollidingactor` | Misc | `f` | 10 |
| `getcollidingpc` | Misc | — → `l` | 9 |
| `showrestmenu` | Gui | — | 9 |
| `wakeuppc` | Misc | — | 9 |
| `hurtstandingactor` | Misc | `f` | 5 |
| `getweapontype` | Container | — → `l` | 4 |
| `payfine` | Misc | — | 4 |
| `togglemenus` | Gui | — | 4 |
| `getblightdisease` | Stats | — → `l` | 3 |
| `menutest` | Gui | `/l` | 3 |
| `onknockout` | Stats | — → `l` | 3 |
| `onmurder` | Stats | — → `l` | 3 |
| `skipanim` | Animation | — | 3 |
| `disablelevitation` | Misc | — | 1 |
| `enablelevitation` | Misc | — | 1 |
| `hitonme` | Misc | `S` → `l` | 1 |
| `streammusic` | Sound | `S` | 1 |

## Engine-written locals nothing raises

Not opcodes, so no call site names them: a script declares `short OnPCEquip` and the ENGINE writes the variable. Every script below reads a local that is always 0.

| Local | Flag | Scripts declaring it |
|---|---|---:|
| `onpchitme` | `pcHitMe` | 160 |
| `onpcequip` | `pcEquipped` | 111 |
| `onpcadd` | `pcAdded` | 43 |
| `onpcdrop` | `pcDropped` | 6 |

## Ported

| Command | Domain | Signature | Calls |
|---|---|---|---:|
| `journal` | Dialogue | `cl` | 11268 |
| `choice` | Dialogue | `j/SlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSlSl` | 8546 |
| `moddisposition` | Stats | `l` | 5711 |
| `getjournalindex` | Dialogue | `c` → `l` | 5665 |
| `additem` | Container | `clX` | 5248 |
| `addtopic` | Dialogue | `S` | 5247 |
| `goodbye` | Dialogue | — | 4810 |
| `removeitem` | Container | `clX` | 3075 |
| `disable` | Misc | `x` | 2905 |
| `getitemcount` | Container | `cX` → `l` | 1866 |
| `getdisabled` | Misc | `x` → `l` | 1856 |
| `startcombat` | Ai | `c` | 1764 |
| `setfight` | Ai | `l` | 1759 |
| `enable` | Misc | `x` | 1263 |
| `positioncell` | Transformation | `ffffczz` | 1232 |
| `aiwander` | Ai | `fff/llllllllll` | 1207 |
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
| `addspell` | Stats | `cz` | 570 |
| `activate` | Misc | `x` | 503 |
| `moveworld` | Transformation | `cf` | 363 |
| `getpccell` | Cell | `c` → `l` | 350 |
| `aitravel` | Ai | `fff/lx` | 292 |
| `playsound3d` | Sound | `cXX` | 280 |
| `cast` | Misc | `SS` | 276 |
| `stopcombat` | Ai | `x` | 276 |
| `aifollow` | Ai | `cffff/llllllll` | 266 |
| `modreputation` | Dialogue | `l` | 262 |
| `sethealth` | Stats | `f` | 245 |
| `placeatme` | Transformation | `clflX` | 241 |
| `getrace` | Stats | `c` → `l` | 238 |
| `sethello` | Ai | `l` | 235 |
| `random` | Misc | `l` → `f` | 214 |
| `gethealth` | Stats | `x` → `f` | 211 |
| `getspell` | Stats | `c` → `l` | 207 |
| `rotate` | Transformation | `cf` | 206 |
| `removespell` | Stats | `cz` | 201 |
| `say` | Sound | `SS` | 184 |
| `placeitemcell` | Transformation | `ccffffX` | 179 |
| `setangle` | Transformation | `cf` | 159 |
| `setdisposition` | Stats | `l` | 159 |
| `unlock` | Misc | — | 152 |
| `getaipackagedone` | Ai | — → `l` | 150 |
| `setalarm` | Ai | `l` | 133 |
| `setdelete` | Misc | `l` | 133 |
| `getbuttonpressed` | Gui | — → `l` | 131 |
| `placeatpc` | Transformation | `clflX` | 128 |
| `equip` | Container | `cX` | 126 |
| `hassoulgem` | Container | `c` → `l` | 123 |
| `geteffect` | Misc | `S` → `l` | 121 |
| `getsoundplaying` | Sound | `c` → `l` | 120 |
| `pcexpell` | Stats | `/S` | 119 |
| `modpccrimelevel` | Stats | `f` | 114 |
| `getpcrank` | Stats | `/S` → `l` | 110 |
| `modfight` | Ai | `l` | 109 |
| `explodespell` | Misc | `S` | 106 |
| `lock` | Misc | `/l` | 101 |
| `playsoundvp` | Sound | `cff` | 97 |
| `drop` | Misc | `cl` | 95 |
| `rotateworld` | Transformation | `cf` | 92 |
| `getpccrimelevel` | Stats | — → `f` | 91 |
| `getdisposition` | Stats | — → `l` | 90 |
| `move` | Transformation | `cf` | 90 |
| `playsound3dvp` | Sound | `cff` | 85 |
| `getspelleffects` | Misc | `c` → `l` | 82 |
| `getcurrentaipackage` | Ai | — → `l` | 75 |
| `getlocked` | Misc | — → `l` | 74 |
| `scriptrunning` | Misc | `c` → `l` | 73 |
| `stopsound` | Sound | `cXX` | 72 |
| `getangle` | Transformation | `c` → `f` | 69 |
| `removesoulgem` | Misc | `c/l` | 64 |
| `playloopsound3dvp` | Sound | `cff` | 59 |
| `setjournalindex` | Dialogue | `cl` | 59 |
| `setfatigue` | Stats | `f` | 58 |
| `getcurrentweather` | Sky | — → `l` | 55 |
| `removeeffects` | Stats | `l` | 54 |
| `getinterior` | Cell | — → `l` | 53 |
| `modcurrentfatigue` | Stats | `f` | 51 |
| `getlos` | Ai | `c` → `l` | 50 |
| `setflee` | Ai | `l` | 50 |
| `gettarget` | Ai | `c` → `l` | 49 |
| `modcurrenthealth` | Stats | `f` | 48 |
| `saydone` | Sound | — → `l` | 44 |
| `getfatigue` | Stats | `x` → `f` | 43 |
| `modrestoration` | Stats | `f` | 43 |
| `setscale` | Transformation | `f` | 42 |
| `getspeechcraft` | Stats | — → `f` | 40 |
| `getdetected` | Ai | `c` → `l` | 39 |
| `pcraiserank` | Stats | `/S` | 39 |
| `getweapondrawn` | Misc | — → `l` | 38 |
| `setpccrimelevel` | Stats | `f` | 37 |
| `face` | Ai | `ffX` | 35 |
| `modmercantile` | Stats | `f` | 33 |
| `position` | Transformation | `ffffz` | 33 |
| `setmagicka` | Stats | `f` | 33 |
| `getpcsneaking` | Control | — → `l` | 31 |
| `setparalysis` | Stats | `l` | 31 |
| `getintelligence` | Stats | — → `f` | 30 |
| `addsoulgem` | Misc | `ccX` | 28 |
| `aiescort` | Ai | `cffff/l` | 28 |
| `getpcsleep` | Misc | — → `l` | 26 |
| `clearforcesneak` | Control | — | 25 |
| `getfight` | Ai | — → `l` | 22 |
| `hasitemequipped` | Container | `c` → `l` | 20 |
| `getstrength` | Stats | — → `f` | 19 |
| `playloopsound3d` | Sound | `cXX` | 19 |
| `resurrect` | Stats | — | 19 |
| `setatstart` | Transformation | — | 19 |
| `getspellreadied` | Misc | — → `l` | 18 |
| `placeitem` | Transformation | `cffffX` | 18 |
| `getsquareroot` | Misc | `f` → `f` | 16 |
| `getlevel` | Stats | — → `l` | 15 |
| `getscale` | Transformation | — → `f` | 15 |
| `raiserank` | Stats | `x` | 15 |
| `setspeed` | Stats | `f` | 15 |
| `modfatigue` | Stats | `f` | 14 |
| `getluck` | Stats | — → `f` | 13 |
| `getmagicka` | Stats | `x` → `f` | 12 |
| `modalteration` | Stats | `f` | 12 |
| `pcjoinfaction` | Stats | `/S` | 12 |
| `forcesneak` | Control | — | 11 |
| `getinvisible` | Stats | — → `l` | 11 |
| `getmercantile` | Stats | — → `f` | 11 |
| `getparalysis` | Stats | — → `l` | 11 |
| `modfactionreaction` | Dialogue | `ccl` | 11 |
| `modcurrentmagicka` | Stats | `f` | 10 |
| `modhealth` | Stats | `f` | 10 |
| `aifollowcell` | Ai | `ccffff/l` | 9 |
| `gethello` | Ai | — → `l` | 9 |
| `getsecurity` | Stats | — → `f` | 9 |
| `pcclearexpelled` | Stats | `/S` | 9 |
| `removespelleffects` | Stats | `c` | 9 |
| `sethandtohand` | Stats | `f` | 8 |
| `setmarksman` | Stats | `f` | 8 |
| `setmercantile` | Stats | `f` | 8 |
| `fall` | Misc | — | 7 |
| `setstrength` | Stats | `f` | 7 |
| `aiactivate` | Ai | `c/l` | 6 |
| `getalchemy` | Stats | — → `f` | 6 |
| `getchameleon` | Stats | — → `l` | 6 |
| `getpcrunning` | Control | — → `l` | 6 |
| `getwillpower` | Stats | — → `f` | 6 |
| `modalchemy` | Stats | `f` | 6 |
| `resetactors` | Transformation | — | 6 |
| `setchameleon` | Stats | `l` | 6 |
| `getforcesneak` | Control | — → `l` | 5 |
| `getillusion` | Stats | — → `f` | 5 |
| `modacrobatics` | Stats | `f` | 5 |
| `modflee` | Ai | `l` | 5 |
| `pcexpelled` | Stats | `/S` → `l` | 5 |
| `setshortblade` | Stats | `f` | 5 |
| `getalarm` | Ai | — → `l` | 4 |
| `getconjuration` | Stats | — → `f` | 4 |
| `getcurrenttime` | Misc | — → `f` | 4 |
| `getpersonality` | Stats | — → `f` | 4 |
| `modscale` | Transformation | `f` | 4 |
| `pclowerrank` | Stats | `/S` | 4 |
| `samefaction` | Dialogue | — → `l` | 4 |
| `setathletics` | Stats | `f` | 4 |
| `setaxe` | Stats | `f` | 4 |
| `setlongblade` | Stats | `f` | 4 |
| `getalteration` | Stats | — → `f` | 3 |
| `getendurance` | Stats | — → `f` | 3 |
| `getsneak` | Stats | — → `f` | 3 |
| `getspear` | Stats | — → `f` | 3 |
| `getspeed` | Stats | — → `f` | 3 |
| `getstartingangle` | Transformation | `c` → `f` | 3 |
| `modshortblade` | Stats | `f` | 3 |
| `setacrobatics` | Stats | `f` | 3 |
| `setagility` | Stats | `f` | 3 |
| `setbluntweapon` | Stats | `f` | 3 |
| `setmediumarmor` | Stats | `f` | 3 |
| `setpcfacrep` | Stats | `l/c` | 3 |
| `getdestruction` | Stats | — → `f` | 2 |
| `gethandtohand` | Stats | — → `f` | 2 |
| `getlongblade` | Stats | — → `f` | 2 |
| `getmysticism` | Stats | — → `f` | 2 |
| `lowerrank` | Stats | `x` | 2 |
| `modagility` | Stats | `f` | 2 |
| `modalarm` | Ai | `l` | 2 |
| `modaxe` | Stats | `f` | 2 |
| `moddestruction` | Stats | `f` | 2 |
| `modenchant` | Stats | `f` | 2 |
| `modlongblade` | Stats | `f` | 2 |
| `modmagicka` | Stats | `f` | 2 |
| `modspear` | Stats | `f` | 2 |
| `modspeechcraft` | Stats | `f` | 2 |
| `setblock` | Stats | `f` | 2 |
| `setwaterbreathing` | Stats | `l` | 2 |
| `aiescortcell` | Ai | `ccffff/l` | 1 |
| `getagility` | Stats | — → `f` | 1 |
| `getarmorer` | Stats | — → `f` | 1 |
| `getflee` | Ai | — → `l` | 1 |
| `getreputation` | Dialogue | — → `l` | 1 |
| `getresistmagicka` | Stats | — → `l` | 1 |
| `getrestoration` | Stats | — → `f` | 1 |
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
| `dropsoulgem` | Misc | `c` |  |
| `getacrobatics` | Stats | — → `f` |  |
| `getarmorbonus` | Stats | — → `l` |  |
| `getathletics` | Stats | — → `f` |  |
| `getattackbonus` | Stats | — → `l` |  |
| `getaxe` | Stats | — → `f` |  |
| `getblindness` | Stats | — → `l` |  |
| `getblock` | Stats | — → `f` |  |
| `getbluntweapon` | Stats | — → `f` |  |
| `getcastpenalty` | Stats | — → `l` |  |
| `getdefendbonus` | Stats | — → `l` |  |
| `getenchant` | Stats | — → `f` |  |
| `getfactionreaction` | Dialogue | `ccX` → `l` |  |
| `getflying` | Stats | — → `l` |  |
| `getheavyarmor` | Stats | — → `f` |  |
| `getlightarmor` | Stats | — → `f` |  |
| `getlineofsight` | Ai | `c` → `l` |  |
| `getmarksman` | Stats | — → `f` |  |
| `getmediumarmor` | Stats | — → `f` |  |
| `getpcfacrep` | Stats | `/c` → `l` |  |
| `getresistblight` | Stats | — → `l` |  |
| `getresistcorprus` | Stats | — → `l` |  |
| `getresistdisease` | Stats | — → `l` |  |
| `getresistfire` | Stats | — → `l` |  |
| `getresistfrost` | Stats | — → `l` |  |
| `getresistnormalweapons` | Stats | — → `l` |  |
| `getresistparalysis` | Stats | — → `l` |  |
| `getresistpoison` | Stats | — → `l` |  |
| `getresistshock` | Stats | — → `l` |  |
| `getshortblade` | Stats | — → `f` |  |
| `getsilence` | Stats | — → `l` |  |
| `getstartingpos` | Transformation | `c` → `f` |  |
| `getsuperjump` | Stats | — → `l` |  |
| `getswimspeed` | Stats | — → `l` |  |
| `getunarmored` | Stats | — → `f` |  |
| `getwaterbreathing` | Stats | — → `l` |  |
| `getwaterwalking` | Stats | — → `l` |  |
| `modarmorbonus` | Stats | `l` |  |
| `modarmorer` | Stats | `f` |  |
| `modathletics` | Stats | `f` |  |
| `modattackbonus` | Stats | `l` |  |
| `modblindness` | Stats | `l` |  |
| `modblock` | Stats | `f` |  |
| `modcastpenalty` | Stats | `l` |  |
| `moddefendbonus` | Stats | `l` |  |
| `modendurance` | Stats | `f` |  |
| `modflying` | Stats | `l` |  |
| `modhandtohand` | Stats | `f` |  |
| `modheavyarmor` | Stats | `f` |  |
| `modhello` | Ai | `l` |  |
| `modillusion` | Stats | `f` |  |
| `modintelligence` | Stats | `f` |  |
| `modinvisible` | Stats | `l` |  |
| `modlightarmor` | Stats | `f` |  |
| `modluck` | Stats | `f` |  |
| `modparalysis` | Stats | `l` |  |
| `modresistblight` | Stats | `l` |  |
| `modresistcorprus` | Stats | `l` |  |
| `modresistdisease` | Stats | `l` |  |
| `modresistfire` | Stats | `l` |  |
| `modresistfrost` | Stats | `l` |  |
| `modresistmagicka` | Stats | `l` |  |
| `modresistnormalweapons` | Stats | `l` |  |
| `modresistparalysis` | Stats | `l` |  |
| `modresistpoison` | Stats | `l` |  |
| `modresistshock` | Stats | `l` |  |
| `modsecurity` | Stats | `f` |  |
| `modsilence` | Stats | `l` |  |
| `modsneak` | Stats | `f` |  |
| `modspeed` | Stats | `f` |  |
| `modsuperjump` | Stats | `l` |  |
| `modswimspeed` | Stats | `l` |  |
| `modwaterbreathing` | Stats | `l` |  |
| `modwaterwalking` | Stats | `l` |  |
| `modwillpower` | Stats | `f` |  |
| `ra` | Transformation | — |  |
| `setalchemy` | Stats | `f` |  |
| `setalteration` | Stats | `f` |  |
| `setarmorer` | Stats | `f` |  |
| `setattackbonus` | Stats | `l` |  |
| `setblindness` | Stats | `l` |  |
| `setcastpenalty` | Stats | `l` |  |
| `setconjuration` | Stats | `f` |  |
| `setdefendbonus` | Stats | `l` |  |
| `setenchant` | Stats | `f` |  |
| `setfactionreaction` | Dialogue | `ccl` |  |
| `setflying` | Stats | `l` |  |
| `setheavyarmor` | Stats | `f` |  |
| `setillusion` | Stats | `f` |  |
| `setintelligence` | Stats | `f` |  |
| `setinvisible` | Stats | `l` |  |
| `setlightarmor` | Stats | `f` |  |
| `setmysticism` | Stats | `f` |  |
| `setpersonality` | Stats | `f` |  |
| `setreputation` | Dialogue | `l` |  |
| `setresistblight` | Stats | `l` |  |
| `setresistcorprus` | Stats | `l` |  |
| `setresistdisease` | Stats | `l` |  |
| `setresistfire` | Stats | `l` |  |
| `setresistfrost` | Stats | `l` |  |
| `setresistmagicka` | Stats | `l` |  |
| `setresistnormalweapons` | Stats | `l` |  |
| `setresistparalysis` | Stats | `l` |  |
| `setresistpoison` | Stats | `l` |  |
| `setresistshock` | Stats | `l` |  |
| `setrestoration` | Stats | `f` |  |
| `setsecurity` | Stats | `f` |  |
| `setsilence` | Stats | `l` |  |
| `setspear` | Stats | `f` |  |
| `setspeechcraft` | Stats | `f` |  |
| `setsuperjump` | Stats | `l` |  |
| `setswimspeed` | Stats | `l` |  |
| `setunarmored` | Stats | `f` |  |
| `setwaterwalking` | Stats | `l` |  |

## Deliberate no-ops

Nothing to port: Morrowind's own presentation, or state this runtime does not keep.

| Command | Domain | Signature | Calls |
|---|---|---|---:|
| `showmap` | Gui | `Sxxxx` | 1741 |
| `clearinfoactor` | Dialogue | — | 628 |
| `fadeout` | Misc | `f` | 126 |
| `fadein` | Misc | `f` | 120 |
| `forcerun` | Control | — | 36 |
| `clearforcerun` | Control | — | 17 |
| `dontsaveobject` | Misc | — | 5 |
| `getforcerun` | Control | — → `l` | 4 |
| `forcejump` | Control | — | 3 |
| `forcemovejump` | Control | — | 3 |
| `clearforcejump` | Control | — | 2 |
| `clearforcemovejump` | Control | — | 2 |
| `getforcemovejump` | Control | — → `l` | 1 |
| `fadeto` | Misc | `ff` |  |
| `fixme` | Transformation | — |  |
| `getforcejump` | Control | — → `l` |  |

## Stubbed, but nothing calls it

Console, debug and chargen commands. Listed for completeness; none is reachable from dialogue.

`addtolevcreature`, `addtolevitem`, `bc`, `becomewerewolf`, `betacomment`, `centeroncell`, `centeronexterior`, `coc`, `coe`, `enablebirthmenu`, `enableclassmenu`, `enableinventorymenu`, `enablelevelupmenu`, `enablemagicmenu`, `enablemapmenu`, `enablenamemenu`, `enableracemenu`, `enablerest`, `enablestatreviewmenu`, `enablestatsmenu`, `filljournal`, `fillmap`, `getcollidingactor`, `getmasserphase`, `getpcinjail`, `getpctraveling`, `getpcvisionbonus`, `getsecundaphase`, `getstat`, `getwerewolfkills`, `help`, `hitattemptonme`, `iswerewolf`, `modpcvisionbonus`, `modregion`, `ori`, `outputrefinfo`, `pcforce1stperson`, `pcforce3rdperson`, `pcget3rdperson`, `playbink`, `reloadlua`, `removefromlevitem`, `repairedonme`, `setlevel`, `setnavmeshnumber`, `setpcvisionbonus`, `setwerewolfacrobatics`, `show`, `showscenegraph`, `showvars`, `ssg`, `sv`, `t3d`, `tai`, `tap`, `tb`, `tcb`, `tcg`, `tcl`, `testcells`, `testinteriorcells`, `testmodels`, `tfh`, `tfow`, `tgm`, `tm`, `toggleactorspaths`, `toggleai`, `toggleborders`, `togglecollision`, `togglecollisionboxes`, `togglecollisiongrid`, `togglefogofwar`, `togglefullhelp`, `togglegodmode`, `togglenavmesh`, `togglepathgrid`, `togglerecastmesh`, `togglescripts`, `togglesky`, `togglevanitymode`, `togglewater`, `togglewireframe`, `toggleworld`, `tpg`, `ts`, `turnmoonred`, `turnmoonwhite`, `tvm`, `tw`, `twa`, `twf`, `undowerewolf`, `user1`, `user2`, `user3`, `user4`, `xbox`
