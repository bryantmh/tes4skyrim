# Quest test coverage (2026-11-23)

How much of each game the headless quest cases exercise, and which questlines pass. The README's
"Tested questlines" chart is drawn from this page.

## Method

`python tools/validate/safe_run.py python -m tools.audit.quest_coverage` prints both tables below;
`--write-readme` regenerates the README's bars after a run.

- **Journal quests:** a TES4 `QUST` with at least one stage log text, or a TES3 journal topic
  (`MWDI DialType=Journal`). Each counts only in the plugin that adds it (masters' ids are subtracted),
  so Tribunal's count is its own quests, not Morrowind's again.
- **Tested:** a case line outside comments names the quest's EditorID (`GetStage MQ02`,
  `GetJournalIndex HH_TwinLamps1`, `SetStage`...), OR a run advanced it: every MorrowindRuntime log in
  `temp/full_run` (`journal: <id> = <index>`, index > 0; the `ingame` sweep excluded) is merged into
  [quest_test_coverage_reached.txt](quest_test_coverage_reached.txt), which keeps the evidence after
  `temp/` is cleared. TESRuntime logs only Skyrim FormIDs of shown objectives and no TES4 run logs are
  kept, so Oblivion, Shivering Isles, Nehrim and Morroblivion count named quests only.
- **Why a complete run is not 100%:** the clean 97/97 Morrowind main-quest run advanced 48 of the 104
  main-quest journals; the rest are branches one playthrough cannot take (17 `A1_Sleepers_*`, the
  `*Kill` / `B8_Failed_Nerevarine` paths, the six `B8_BackDoor*` routes, `CX_BackPath`).
- **Confidence** (`GAMES` in the tool) is a judgment of how likely an untested quest works: high where
  long clean runs prove the shared dialogue, script and AI conversion (the Morrowind-format games), medium
  for Oblivion / Shivering Isles / Morroblivion, low for Nehrim (no clean run yet).
- **Folder to plugin:** `tools/live/full_run.py` `STAGES` (the plugins each lane loads). A folder missing
  from there (`oblivion_si`, `oblivion_conversations`, `morroblivion_guilds`, `morrowind_chargen_quiz`)
  takes the stage its name starts with. `ingame` (the MWScript opcode sweep) is not a quest folder.
- **Questlines (README view, `--readme`):** each journal id goes to a questline by its authored prefix
  (`MQ`, `FG`, `HH_`, `tr_m1_HT_`, Morroblivion's `fbmw` + the Morrowind id); ids no questline claims
  are Misc. Shivering Isles is Oblivion.esm's `SE*` ids, its main quest `SE01`-`SE14`. ✅ marks the
  questlines in the tool's `DONE` list: ones with their own case folder that passes start to finish.
- Pass/fail status comes from the run worklogs, not from this tool:
  [mwscript_full_runs.md](mwscript_full_runs.md) (Morrowind-format lanes) and
  [oblivion_quest_runs.md](oblivion_quest_runs.md) (TES4).

## Journal quests checked, per plugin

| Plugin | Journal quests | Checked by a case | Share |
|---|---:|---:|---:|
| Oblivion.esm (Shivering Isles included) | 252 | 29 | 12% |
| ... of which Shivering Isles (`SE*` quests) | 34 | 9 | 26% |
| Nehrim.esm | 84 | 8 | 10% |
| Morrowind.esm | 632 | 150 | 24% |
| Tribunal.esm | 59 | 31 | 53% |
| Bloodmoon.esm | 68 | 35 | 51% |
| Arktwend_English.esm | 123 | 45 | 37% |
| Morrowind_ob.esm (Morroblivion) | 599 | 1 | 0% |
| Tamriel Rebuilt 25.08.12 | 1,322 | 37 | 3% |
| Tamriel Data (HD) | 3 | 0 | 0% |
| **All** | **3,142** | **336** | **11%** |

Fallout 3 and New Vegas have no quest cases yet.

## Case folders

| Folder | Lane | Cases | Quests named | Status |
|---|---|---:|---:|---|
| oblivion_main | oblivion | 95 | 19 | Two clean back-to-back new-game runs (95/95 x2), then a recorded clean run |
| oblivion_si | oblivion | 56 | 9 | SE01 (The Door) passes; SE02 in progress; SE03-SE06 drafted, not run |
| oblivion_conversations | oblivion | 2 | 2 | No recorded result |
| nehrim_main | nehrim | 47 | 8 | No recorded full run |
| morrowind_main | morrowind | 97 | 45 | Morrowind lane clean 443/443 twice (runs 20, 21) |
| morrowind_hlaalu | morrowind | 46 | 31 | " |
| morrowind_thieves_guild | morrowind | 42 | 30 | " |
| morrowind_vampire_aundae / berne / quarra | morrowind | 25 | 7 | " |
| morrowind_disease | morrowind | 2 | 0 | " |
| morrowind_temple | tribunal | 70 | 30 | " |
| morrowind_daedric | tribunal | 24 | 7 | " |
| tribunal_story | tribunal | 17 | 20 | " |
| tribunal_mournhold | tribunal | 11 | 12 | " |
| bloodmoon_main | bloodmoon | 18 | 18 | " |
| raven_rock, raven_rock_brawl | bloodmoon | 19 | 17 | " |
| morrowind_chargen_quiz | morrowind | 1 | 0 | Not in the full run |
| arktwend_main | arktwend | 191 | 44 | Clean 191/191 recorded run (run 68); accepted at 190/191 x3 |
| arktwend_side | arktwend | 6 | 1 | 6/6 in every run since run 13 |
| morroblivion_main, morroblivion_quiz | morroblivion | 14 | 2 | Morroblivion lane clean 122/122 twice (runs 16, 19) |
| tr_telvanni | morroblivion | 89 | 32 | " |
| tr_necrom | morroblivion | 19 | 5 | " |
| morroblivion_guilds | morroblivion | 1 | 0 | Fighters Guild advancement 3/3 |

## Untested quests with a follower or escort

From `quest_coverage --followers`: untested journal quests whose dialogue result or script runs
`AiFollow` / `AiEscort` (Morrowind-format plugins). These are the quests that depend on navmesh in
places no run has walked. The second column is the follower, or the script that starts the follow.

### Arktwend_English.esm (7)

| Quest | Follower |
|---|---|
| `a_i_jert` | Jert die Klaue |
| `a_i_pyter` | Pyter |
| `a_ii_reisebegleiter` | AlterBettler |
| `a_iv_fortronan` | FortRonanSoldaten |
| `a_iv_stadtwache` | STADFlucke, STADRangordScript, StadtwacheFlucke01 |
| `a_vi_florian` | FlorianderAbenteuer01 |
| `nq_tommy` | NQTommy |

### Bloodmoon.esm (4)

| Quest | Follower |
|---|---|
| `bm_frostgiant2` | krishScript |
| `bm_ingmar` | ingmar |
| `bm_missionary` | mirisa |
| `co_9a` | ColonyHroldar_B, Tirvel Balen |

### Morrowind.esm (33)

| Quest | Follower |
|---|---|
| `11111 test journal` | Example_NPC_Stuff |
| `fg_dissaplamine` | teres arothan |
| `fg_nchurdamz` | Larienna Macrina |
| `fg_telasero` | sondaale of shimmerene |
| `fg_vas` | ulyne henim |
| `hr_arobarkidnap` | nartise arobar |
| `hr_finddalobar` | mathis dalobar |
| `hr_koalcave` | deval beleth |
| `hr_ransommandas` | delyna mandas |
| `hr_stronghold` | frelene acques |
| `ht_dahrkmezalf` | baladasCenturion, centurion_shock_baladas |
| `il_damsel` | Dandsa |
| `il_rescuehermit` | assaba-bentus |
| `il_rescuepilgrim` | madura seran |
| `il_rescueragash` | ragash gra-shuzgub |
| `mg_escortscholar1` | tenyeminwe |
| `ms_arenimtomb` | Satyana |
| `ms_hannat` | hannat zainsubani |
| `ms_whiteguar` | whiteguarScript |
| `mv_abusedhealer` | Hlormar Wine-Sot, sosia caristiana |
| `mv_cultistvictim` | Malexa, malexa |
| `mv_fakeslave` | tul |
| `mv_missingcompanion` | Drerel Indaren, drerel indaren |
| `mv_poorpilgrim` | Fonus Rathryon |
| `mv_recoverwidowmaker` | Botrir |
| `mv_richpilgrim` | Viatrix Petilia |
| `mv_richtrader` | Teris Raledran |
| `mv_runawayslave` | Reeh_Jah |
| `mv_slavemule` | Rabinna |
| `mv_strayedpilgrim` | Edras Oril, edras_oril |
| `mv_traderabandoned` | Pemenie |
| `mv_tradermissed` | Paur Maston |
| `mv_wanderingpilgrim` | Nevrasa Dralor |

### Tamriel Rebuilt 25.08.12 (125)

| Quest | Follower |
|---|---|
| `tr_m1_fg_codaliastalker` | TR_m1_Garrulus |
| `tr_m1_go_5` | TR_m1_Q50_GO5_Husband, TR_m1_q50_GO5_TrueHusband_scpt |
| `tr_m1_go_6` | TR_m1_Gamarth |
| `tr_m1_gs_mq_3` | TR_m1_Anirwen |
| `tr_m1_gs_mq_5` | TR_m1_GS_MQ_5_CompanionA, TR_m1_GS_MQ_5_CompanionB |
| `tr_m1_il_pahunsabi` | TR_m1_Darnell |
| `tr_m1_pt_adh` | TR_m1_Q59_ADH_script_6 |
| `tr_m1_pt_ogrim` | TR_m1_Q59_HaO_Brendu |
| `tr_m1_rr_drunkard` | TR_m1_Hasilius_Brahn |
| `tr_m1_rr_mq_10` | TR_m1_q_SimethFollow |
| `tr_m1_rr_mq_5` | TR_m1_q_ArgonianHitman1, TR_m1_q_ArgonianHitman2 |
| `tr_m1_to_acoldharbourkeelhaul` | TR_m1_noga-togroi |
| `tr_m1_to_runningintotrouble` | TR_m1_S_Mee_Sei |
| `tr_m1_tt_2` | TR_m1_q_SlaveDiseased |
| `tr_m1_tt_2_status` | TR_m1_q_SlaveDiseased |
| `tr_m1_tt_4` | TR_m1_q_Pilgrim1, TR_m1_q_Pilgrim2 |
| `tr_m1_wil_escorttoto` | TR_m1_Shavra_gra-Vatok |
| `tr_m1_wil_smuggleseeker` | TR_m1_Shanath Teran |
| `tr_m2_a8_5_freeatlast` | TR_m2_q_A8_5_Shamirr_sc |
| `tr_m2_ao_shackledlove` | TR_m2_q_9_NilenoScript |
| `tr_m2_fg_amiro3` | TR_m2_q_27_heelkur |
| `tr_m2_fg_hartise_rethyn` | TR_m2_FG_Rethynscript |
| `tr_m2_fg_hartise_selyn` | TR_m2_FG_Rethynscript |
| `tr_m2_fg_hartise_selyn2` | TR_m2_FG_Rethynscript |
| `tr_m2_fg_hartise_tainted` | TR_m2_Arfil |
| `tr_m2_mar_saveslave` | TR_m2_Jeela |
| `tr_m2_tmo_qualitytime` | TR_m2_q_5_Mohryl |
| `tr_m2_wil_avengeknights` | TR_m2_Merias Tolode |
| `tr_m2_wil_faith` | TR_m2_q_18_Maenasi |
| `tr_m2_wil_hlersis` | TR_m2_Ararvy Orethera |
| `tr_m2_wil_ruinofstrife` | TR_m2_BogamulgroGulfis, TR_m2_O_Ushugoba |
| `tr_m2_wil_whichwitch` | TR_m2_q_12_AlfhedScript, TR_m2_q_12_Rianele |
| `tr_m3_aim_dreugh2` | TR_m3_Ivon Seloth, TR_m3_NPC_Aim_IndGrd_scp |
| `tr_m3_at_deed` | TR_m3_q_Deed_Script_Slaves |
| `tr_m3_bala_wlcr` | TR_m3_Dolvys Teloth |
| `tr_m3_bo_burglar2` | TR_m3_q_3_Captain_sc |
| `tr_m3_fg_at_5` | TR_m4_NPC_S_Razamar, TR_m4_NPC_S_Varvaakhu |
| `tr_m3_fg_at_5_b` | TR_m4_NPC_S_Razamar |
| `tr_m3_fg_at_5_c` | TR_m4_NPC_S_Varvaakhu |
| `tr_m3_go_guard2` | TR_m3_Julie |
| `tr_m3_rd_extpunish` | TR_m3_RD_q_EJP_TalenSC |
| `tr_m3_tg_fentus3d` | TR_m3_OE_HhontjulfDog |
| `tr_m3_tg_fentus4` | TR_m3_Karandia Corrux |
| `tr_m3_tt_illene1` | TR_m3_Llevari_Uvelar |
| `tr_m3_tt_lloris5b` | TR_m3_Calitia |
| `tr_m3_wil_hvaldur` | TR_m3_NPC_HvaldurScript |
| `tr_m4_arv_stolencargo` | TR_m4_NPC_HisHeartClay_Scp, TR_m4_S_His-Heart-Clay |
| `tr_m4_arv_vendelvisit` | TR_m4_NPC_UvaynMessenger |
| `tr_m4_arv_vendelvisitd` | TR_m4_NPC_UvaynMessenger |
| `tr_m4_bal_killorlukh` | TR_m4_VA_VarosSc |
| `tr_m4_bd_lost_in_transit` | TR_m4_q_Amandin_Platier, TR_m4_q_Amandin_Platier_sc |
| `tr_m4_bd_lost_in_transit_a` | TR_m4_q_Amandin_Platier |
| `tr_m4_bd_lost_in_transit_c` | TR_m4_q_Amandin_Platier, TR_m4_q_Amandin_Platier_sc |
| `tr_m4_bd_lost_in_transit_d` | TR_m4_q_Amandin_Platier_sc |
| `tr_m4_fg_alits` | TR_m4_NPC_Darra |
| `tr_m4_fg_alitsb` | TR_m4_NPC_Darra |
| `tr_m4_fg_darvas` | TR_m4_Jubal_Orda |
| `tr_m4_hh_and_reverserescue` | TR_m4_HH_RescueScript |
| `tr_m4_hh_and_reverserescue2` | TR_m4_HH_RescueScript |
| `tr_m4_il_agentb` | TR_m4_Raynil_Vando |
| `tr_m4_mg_ando_7` | TR_m4_q_Adric Jerenise |
| `tr_m4_om_remnantspath2` | TR_m4_Llayno_Erendi |
| `tr_m4_tg_rilmas2` | TR_m4_Marug2, TR_m4_Tattara2, TR_m4_rowdy rilmas |
| `tr_m4_tg_shei1` | TR_m4_Marug2, TR_m4_Tattara2, TR_m4_rowdy rilmas |
| `tr_m4_tg_shei3` | TR_m4_Marug2, TR_m4_Tattara2, TR_m4_rowdy rilmas |
| `tr_m4_tg_shei3b` | TR_m4_Marug2, TR_m4_Tattara2, TR_m4_rowdy rilmas |
| `tr_m4_tg_shei3c` | TR_m4_Marug2, TR_m4_Tattara2, TR_m4_rowdy rilmas |
| `tr_m4_tg_shei3d` | TR_m4_Marug2, TR_m4_Tattara2, TR_m4_rowdy rilmas |
| `tr_m4_tg_shei5` | TR_m4_NPC_JoKaar, TR_m4_S_Jo'Kaar |
| `tr_m4_tg_shei6` | TR_m4_Marug2, TR_m4_Tattara2, TR_m4_rowdy rilmas |
| `tr_m4_tg_tattara1` | TR_m4_Marug2, TR_m4_Tattara2, TR_m4_rowdy rilmas |
| `tr_m4_tg_tattara2` | TR_m4_Marug2, TR_m4_Tattara2, TR_m4_rowdy rilmas |
| `tr_m4_tg_threeeyes2` | TR_m4_Marug2, TR_m4_Tattara2, TR_m4_rowdy rilmas |
| `tr_m4_tg_threeeyes2b` | TR_m4_Marug2, TR_m4_Tattara2, TR_m4_rowdy rilmas |
| `tr_m4_tt_andothrencharity` | TR_m4_TT_Ravur_Othravel_sc |
| `tr_m4_tt_andothrencharitymels` | TR_m4_TT_Ravur_Othravel |
| `tr_m4_tt_armunadventure` | TR_m4_TT_OrcVivecScript |
| `tr_m4_tt_armunadventure_b` | TR_m4_TT_OrcVivecScript |
| `tr_m4_tt_armunadventure_e` | TR_m4_TT_OrcVivecScript |
| `tr_m4_tt_booksbarbarians` | TR_m4_TT_OrcVivecScript, TR_m4_TT_Ravur_Othravel_sc |
| `tr_m4_tt_forgiveness` | TR_m4_TT_Ravur_Othravel_sc |
| `tr_m4_tt_kitchensupplies` | TR_m4_TT_Ravur_Othravel_sc |
| `tr_m4_tt_lastwillandtestament` | TR_m4_TT_Marja_Iannus, TR_m4_TT_Nalvs_Andolin, TR_m4_TT_Ravur_Othravel_sc |
| `tr_m4_tt_lastwillandtestament_c` | TR_m4_TT_Ravur_Othravel_sc |
| `tr_m4_tt_stf_destroy_ancient` | TR_m4_VA_VarosSc |
| `tr_m4_tt_troublesomeorc` | TR_m4_TT_OrcVivecScript, TR_m4_TT_Ravur_Othravel_sc |
| `tr_m4_tt_wellnesscheck` | TR_m4_TT_Ravur_Othravel_sc |
| `tr_m4_uman_b2` | TR_m4_Dira Folvani |
| `tr_m4_va_ironandblood` | TR_m4_Eraris_Verenim |
| `tr_m4_va_namira` | TR_m4_VA_VarosSc |
| `tr_m4_wil_banditduo` | TR_m4_Agdistrius Rictor |
| `tr_m4_wil_catcatchers` | TR_m4_NPC_Shenjirra |
| `tr_m4_wil_catcatchersb` | TR_m4_NPC_Shenjirra |
| `tr_m4_wil_ganahiru` | TR_m4_Hansi |
| `tr_m4_wil_hautharmokill` | TR_m4_Oirdo Malamartle |
| `tr_m4_wil_householdslave` | TR_m4_NPC_Jerra |
| `tr_m4_wil_mannuscamps` | TR_m4_Atreno Drenim |
| `tr_m4_wil_mydearfriendarvena` | TR_m4_q_Varzan |
| `tr_m4_wil_nakedwitch` | TR_m4_Uvisea Athin |
| `tr_m4_wil_oranescaped2` | TR_m4_NPC_awfully_swift |
| `tr_m4_wil_skeletonwizard` | TR_m4_Cr_SaylenusRamaril_scp |
| `tr_m4_wil_skeletonwizardc` | TR_m4_Cr_SaylenusRamaril_scp |
| `tr_m4_wil_taxmanindalruhn` | TR_m4_Tullo Verotus |
| `tr_m4_wil_tribunalts` | TR_m4_q_Corane_Morianne, TR_m4_q_TTSCorane_scrpt |
| `tr_m7_hh_raran2` | TR_m7_NPC_DrabashtiScript |
| `tr_m7_hl_affairsheart` | TR_m7_Ulyne Rothalen |
| `tr_m7_hl_provingfaith` | TR_m7_Ernest Cergelle |
| `tr_m7_ho_passagemenevia` | TR_m7_Cemaron |
| `tr_m7_ho_tt_03` | TR_m7_Malkur Raran |
| `tr_m7_ho_tt_04` | TR_m7_q_Ahabhu |
| `tr_m7_ho_tt_07` | TR_m7_Gilner Herano |
| `tr_m7_hr_aldiuval_q2` | TR_m7_NPC_Nemendoril |
| `tr_m7_jns_theboss_endoftheline` | TR_m7_Mervis Llarel |
| `tr_m7_ns_ic6` | TR_m7_Vartis Delvi |
| `tr_m7_ns_il_4` | TR_m7_Ernasi Viran |
| `tr_m7_ns_irwaen` | TR_m7_NS_Irwaen_NpcSc |
| `tr_m7_ns_jns_4` | TR_m7_Ns_JNS_4_ScAndera |
| `tr_m7_ns_robbingancestors` | TR_m7_Arniel Duront, TR_m7_NPC_Arniel_Duront |
| `tr_m7_oth_bugtracker` | TR_m7_Oth_BugTest1, TR_m7_Oth_BugTest2, TR_m7_Oth_BugTest3 |
| `tr_m7_shisha_il_1` | TR_m7_Bedras Fels |
| `tr_m7_shisha_il_2` | TR_m7_Meldras Avilo |
| `tr_necmq_2path` | TR_NecMQ_Nerevar_BoneWa, TR_m2_Alvu Sareleth, TR_m2_Erendes Hlervan, TR_m2_Simine Turethi |
| `tr_necmq_3` | TR_NecMQ_Nerevar_BoneWa, TR_m2_Alvu Sareleth, TR_m2_Erendes Hlervan, TR_m2_Simine Turethi |
| `tr_necmq_3tribunal` | TR_NecMQ_Nerevar_BoneWa, TR_m2_Alvu Sareleth, TR_m2_Erendes Hlervan, TR_m2_Simine Turethi |
| `tr_necmq_3visions` | TR_NecMQ_Nerevar_BoneWa, TR_m2_Alvu Sareleth, TR_m2_Erendes Hlervan, TR_m2_Simine Turethi |

### Tribunal.esm (2)

| Quest | Follower |
|---|---|
| `merc_calvus` | Calvus Horatius |
| `ms_holyelf` | high_pockets |
