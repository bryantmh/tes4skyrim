ID		Name	Arguments
0	 	GameMode	 
1	 	MenuMode	Integer
2	ref.	OnActivate	Actor
3	ref.	OnAdd	Container
4	ref.	OnEquip	Container
5	ref.	OnUnequip	Container
6	ref.	OnDrop	Container
7	ref.	SayToDone	Topic
8	ref.	OnHit	Actor
9	ref.	OnHitWith	UNK:53:ObjectID
10	ref.	OnDeath	Actor
11	ref.	OnMurder	Actor
12	ref.	OnCombatEnd	 
13	ref.	Unused	 
14	ref.	Unused2	 
15	ref.	OnPackageStart	UNK:29:Package
16	ref.	OnPackageDone (OnPackageEnd)	UNK:29:Package
17	ref.	ScriptEffectStart	 
18	ref.	ScriptEffectFinish	 
19	ref.	ScriptEffectUpdate	 
20	ref.	OnPackageChange	UNK:29:Package
21	ref.	OnLoad	 
22	ref.	OnMagicEffectHit	UNK:31:Magic Effect
23	ref.	OnSell	Actor
24	ref.	OnTrigger	UNK:4:ObjectReferenceID
25	ref.	OnStartCombat	Actor
26	ref.	OnTriggerEnter	UNK:4:ObjectReferenceID
27	ref.	OnTriggerLeave	UNK:4:ObjectReferenceID
28	ref.	OnActorEquip	UNK:21:ObjectID
29	ref.	OnActorUnequip	UNK:21:ObjectID
30	ref.	OnReset	 
31	ref.	OnOpen	 
32	ref.	OnClose	 
33	ref.	OnGrab	 
34	ref.	OnRelease	 
35	ref.	OnDestructionStageChange	Integer
Console Commands
ID		Name	Arguments
256	 	Show (TST)	Float
 	Show global scripts and variables.
257	ref.	ShowVars (SV)	 
 	Show variables on object. [player->sv]
258	 	ShowGlobalVars (SGLV)	 
 	Show all global variables.
259	 	ShowQuestVars (SQV)	Quest
 	Show quest variables. [svq QuestID]
260	 	ShowQuests (SQ)	 
 	List quests.
261	 	ShowQuestAliases	Quest
 	Show quest aliases. [ShowQuestAliases QuestID]
262	 	SetPapyrusQuestVar (SETPQV)	Quest, String, String
 	Set a Papyrus property on the specified quest.
263	 	SetPapyrusVar (SETPV)	String, String
 	Set a Papyrus property on the selected ref.
264	 	SetQuestAliases	Quest
 	Set quest aliases. [SetQuestAliases QuestID]
265	 	ClearQuestAliases	Quest
 	Clears quest aliases. [ClearQuestAliases QuestID]
266	 	ToggleCombatStats (TCS)	 
267	 	ToggleSafeZone (TSZ)	 
 	Display the television 85% safe zone.
268	 	ToggleAI (TAI)	Actor
269	 	ToggleCollisionGeometry (TCG)	 
 	Show collision geometry.
270	 	ToggleMaterialGeometry (TMG)	 
 	Show material geometry.
271	 	ToggleStairsGeometry (TSG)	 
 	Show stairs geometry.
272	 	ToggleBorders (TB)	 
 	Show border lines for each cell.
273	 	ToggleSky (TS)	 
274	 	ToggleWireframe (TWF)	 
 	Show the world as wireframe.
275	 	ToggleCollision (TCL)	 
276	 	ToggleDebugText (TDT)	 
 	Show debug numbers on the screen.
277	 	ToggleMenus (TM)	 
 	Hide all the menus. Used for taking screen shots.
278	 	TogglePrimitives (TPR)	 
279	 	ShowScenegraph (SSG)	 
 	Create a window with the full game scene graph or the scene graph of the selected reference.
280	 	ShowScenegraph (SFSG)	 
 	Create a window with the full game scene graph, open to the node corresponding to the selected reference (if any).
281	 	ToggleMagicStats (TMS)	 
282	ref.	ShowAnim (SA)	 
 	Show Animation and Actor status.
283	 	Show1stPerson (S1ST)	 
 	Show the 1st person Model from the 3rd person camera. If in 3rd person mode it will show both.
284	 	Help	String, Integer
 	Show this help.
285	 	ToggleNavMesh (TNM)	Integer
 	Toggle blocked display.
286	 	TogglePathLine (TPL)	String
 	Toggle path display.
287	 	PickRefByID (PRID)	UNK:4:ObjectReferenceID
 	Select a reference by id for the console.
288	 	SetLightingPasses (SLP)	String
 	4 (0/1) values. amb|diff|tex|spec ex: 1010
289	 	ToggleLODLand (TLL)	String, String, String, String
290	 	ToggleMapEffect (TME)	String
291	 	SetLODObjectDistance (SLOD)	Float, Float, Float, Float
292	 	ToggleGrassUpdate (TGU)	 
293	 	CenterOnCell (COC)	String
294	 	SetGameSetting (SetGS)	String, String
295	 	SetINISetting (SetINI)	String, String
 	"setting:category" value
296	 	GetINISetting (GetINI)	String
 	"setting:category"
297	 	CenterOnExterior (COE)	Integer, Integer
298	ref.	CalcPathToPoint (P2P)	 
299	ref.	CalcLowPathToPoint (LP2P)	Integer, Integer, Integer
 	ignore locks, allow disabled doors, ignore min use
300	 	SetFog	Integer, Integer
 	2 floats, start and end depths
301	 	SetClipDist	Float
 	float, new clip distance
302	 	ToggleShadowVolumes (TSV)	 
303	 	SetImageSpaceGlow (SISG)	Integer, Float, Float, Float
304	 	ToggleDetection (TDETECT)	 
305	 	ToggleHighProcess (THIGHPROCESS)	 
306	 	ToggleLowProcess (TLOWPROCESS)	 
307	 	ToggleMiddleLowProcess (TMLOWPROCESS)	 
308	 	ToggleMiddleHighProcess (TMHIGHPROCESS)	 
309	 	ToggleAiSchedules (TAIS)	 
310	ref.	SpeakSound	String:Filename, Integer:Emotion, Integer:EmotionValue
311	 	BetaComment (BC)	String
 	Add comment to [General] 'sBetaCommentFile' file. NOTE: select object first. [bc "This rock is too high."]
312	 	GameComment (GC)	String
 	Adds a comment about the game to the GameCommentDB. (Ex. This monster is too hard)
313	 	ToggleCellNode (TCN)	Integer
 	Toggle 3D for a Cell child node: 0-Actor, 1-Marker, 2-Land, 3-Water, 4-Static, 5-Active
314	 	ToggleTrees (TT)	 
 	Turn trees on/off
315	 	SetCameraFOV (FOV)	Float, Float
 	Change the camera's field of view (in deg): default 75
316	 	ToggleGodMode (TGM)	 
 	Toggle God mode
317	 	RefreshShaders (RS)	String
 	Reload HLSL shaders from disk
318	 	ToggleScripts (TSCR)	 
 	Turn Script processing on/off
319	 	ToggleConversations (TCONV)	 
 	Toggle conversation stats
320	 	ToggleFullHelp (TFH)	 
 	Toggle Full Help
321	 	ShowQuestLog (SQL)	Integer
 	Show Quest Log
322	 	ShowFullQuestLog (SFQL)	Quest
 	Show all log entries for a single quest
323	 	DumpTexturePalette (DTP)	String
 	Dump texture palette contents to file param is sort type f-filename, s-size, c-count)
324	 	DumpModelMap (DMM)	String
 	Dump model map contents to file
325	 	CenterOnWorld (COW)	UNK:27:WorldSpace, Integer:Cell X, Integer:Cell Y
 	[COW worldname -10 5]
326	 	ToggleGrass (TG)	 
 	Toggle grass display.
327	 	CreateGrassAt (cga)	Float, Float
328	ref.	AddFaceAnimNote (AFAN)	String
329	 	RefreshINI (REFINI)	 
 	Refresh INI settings from file.
330	 	ToggleEmotions (temo)	 
 	Toggle NPC facial emotions.
331	 	AddDecal	 
332	 	TestCode	Float, Float
333	 	PlayerSpellBook (psb)	 
 	Add all spells to player.
334	 	PrintAiList (pai)	 
 	Printed Ai Lists.
335	 	ShowQuestTargets (SQT)	 
 	Show current quest targets
336	 	HairTint	Integer, Integer, Integer
 	3 ints, RGB
337	 	SaveGame (save)	String, Integer
 	SaveGame <filename>
338	 	LoadGame (load)	String, Integer
 	LoadGame <filename>
339	 	TestAllCells (TAC)	Integer
 	Test All Cells (0 - stop, 1 - start, 2 - interiors, 3 - current world)
340	 	RenderTestCell (rtc)	 
 	runs Render Test on player's current parent cell
341	 	RenderTestHere (rth)	 
 	runs Render Test on player's current position
342	 	SaveWorld	String
 	Save hkWorld <filename>
343	 	ReloadCurrentClimate (rcc)	 
 	Reloads values from the current climate
344	 	ReloadCurrentWeather (rcw)	 
 	Reloads values from the current weather
345	 	TestSeenData (tsd)	 
 	Visually displays the current seen data
346	 	TestLocalMap (tlm)	Integer
 	Simulates the local map. (1 or 0 for FOW on or off)
347	 	MoveToQuestTarget (movetoqt)	Quest, Integer
 	Move player to current quest target.
348	 	PrintNPCDialog (pdialog)	 
 	Prints NPC dialog
349	 	ShowSubtitle	Integer
 	show all dialog subtitles (1 shows always, 0 hides always)
350	 	ShowRenderPasses (srp)	 
 	display render passes for the next frame
351	 	FreezeRenderAccumulation (fra)	 
 	only re-render geometry visible during this frame
352	 	ToggleOcclusion (tocc)	 
 	toggle occlusion query for geometry
353	 	OutputMemContexts (omc)	String
 	Output Mem Context info to a file
354	 	OutputMemStats (oms)	String
 	Output Mem Stats info to a file
355	 	SetMemCheckPoint (smcp)	Integer, String
 	Set the current memory checkpoint (value, optional context name)
356	 	GetMemCheckPoint (gmcp)	String
 	Get the current memory checkpoint (optional context name)
357	 	IncMemCheckPoint (imcp)	String
 	Increment the current memory checkpoint (optional context name)
358	 	OutputAllocations (outalloc)	String, String, Integer, Integer
 	Output memory allocations (file, optional context name, optional start context, optional end context)
359	 	AddWatchAddress (awa)	String, Integer
 	Adds an address range to watch out for.
360	 	RemoveWatchAddress (rwa)	String
 	Removes an address range that we no longer want to watch.
361	 	ToggleDetectionStats (tds)	Actor
 	Show the detection stats of the current seletected Ref.
362	 	SetTargetRefraction (str)	Float
 	Sets the refractive value of the target
363	 	SetTargetRefractionFire (strf)	Float, Integer:Int
 	Sets the refractive fire value of the target
364	 	ToggleRefractionDebug (trd)	 
 	Toggles refraction debug render texture
365	 	ToggleCharControllerShape (TCCS)	 
 	Toggle char controller shape type.
366	ref.	ShowHeadTrackTarget (SHeadT)	 
 	Show the head track target if set from look function
367	 	SetCinematicParam (SCP)	Float, Float, Float
 	Sets various values for the imagespace cinematics
368	 	SetTintParam (STP)	Float, Float, Float, Float
 	Sets various values for the imagespace tint
369	 	SetHDRParam (SHP)	Float, Float, Float, Float, Float, Float, Float, Float, Float
 	Sets various values for the HDR shader
370	ref.	VisualRefPosition (VRP)	 
 	Places and axis object at the ref position.
371	ref.	ShowPivot (SP)	 
 	Puts a temporary yellow plus at the pivot point of the selected reference.
372	 	PrintHDRParam (PHP)	 
 	Prints current HDR settings.
373	 	ToggleHDRDebug (THD)	 
 	Toggles HDR debug textures.
374	 	RevertWorld (rw)	 
 	Revert the world
375	 	OutputArchiveProfile (oap)	String
 	Output Archive profile info to a file
376	 	QuitGame (qqq)	 
 	Exit game without going through menus.
377	 	SaveIniFiles (saveini)	 
 	Writes all the .ini files.
378	 	SetDebugText (sdt)	String, Integer
 	Sets which debug text is shown.
379	 	ToggleLiteBrite (tlb)	 
 	Toggles lite brite render mode.
380	 	RunMemoryPass (rmp)	Integer
 	Runs a cleanup memory pass
381	 	ResetMemContexts (rmc)	 
 	Reset Max Mem Contexts
382	 	ToggleWaterSystem (TWS)	 
 	Toggles the water system
383	 	ToggleWaterRadius (TWR)	 
384	 	ShowWhoDetectsPlayer (SWDP)	 
 	Show who detects the player
385	 	ToggleCombatAI (tcai)	 
 	Toggles ALL Combat AI
386	 	ToggleFlyCam (tfc)	Integer
 	Toggles the Free Fly camera (UFO cam).
387	 	ModWaterShader (mws)	String, Float
 	Modifies water shader settings
388	 	WaterColor (mwc)	Integer, Float, Float, Float
 	Modifies water color
389	 	BeginTrace (bt)	 
 	Creates a trace file (Xenon only)
390	 	RunCellTest (rct)	Integer
 	Runs a cell test
391	 	StartAllQuests (saq)	 
 	Starts all quests
392	 	CompleteAllQuestStages (caqs)	 
 	Sets all quest stages
393	 	ToggleFogOfWar (TFOW)	 
 	Turns fog of war on or off.
394	 	OutputLocalMapPictures (OLMP)	 
 	Writes out the current local map.
395	 	SetGamma (sg)	Float
 	Sets new gamma ramp.
396	 	WasteMemory (wm)	Float
 	Allocates some memory (in MB).
397	 	ClearAdaptedLight (cal)	 
 	Clears the HDR adapted light texture
398	 	ToggleCastShadows (tsh)	Integer
399	 	SetHudGlowConstants (shgc)	Float, Float, Float, Float
 	Set parameters for HUD glow.
400	 	CloseFile	String
401	 	SetTreeMipmapBias (stmb)	Float, Float
 	Set mipmap LOD bias values for tree billboards.
402	 	SendSherlockDebugText (ssdt)	String
403	 	OutputMemStats (omsfw)	String
 	Output Mem Stats info to a file
404	 	PlayExplosion	 
 	Plays explosion seq in selected obj
405	 	SetMaxAniso (sma)	Float
 	Sets Depth of Field focal distance
406	 	SetMeshLODLevel (smll)	Float
 	Sets Depth of Field range
407	 	SetTargetDOF (stdof)	 
 	Sets the depth of field based on the target
408	 	ToggleFullScreenMotionBlur (tfsmb)	 
 	Toggles Full screen motion blur
409	 	SetTriLinearThreshold (stlt)	Float
 	Set the motion blur params
410	 	ToggleFootIK (tfik)	 
 	Toggles Foot IK system
411	ref.	SetVel	UNK:8:Axis, Float
 	Sets Velocity on a reference
412	ref.	DebugCombatBehavior (dcb)	 
 	Debugs the combat behavior on an actor
413	 	ToggleCombatDebug (tcd)	String
 	Toggles combat debug info
414	 	TogglePathingInfoFunction (TPI)	 
 	Toggle pathing information.
415	ref.	ShowInventory (inv)	 
 	Shows reference inventory
416	 	ResetPerformanceTimers (rpt)	 
 	Resets the performance timers
417	 	ToggleDebugDecal (TDD)	Integer
 	Toggle debug displays for decal creation : 0-Wireframe, 1-Solid, 2-Occlusion Query, 3-Transform
418	 	ToggleDecalRendering (TDR)	 
 	Toggle decal rendering
419	 	SetImageSpaceModifiersEnable (sisme)	Integer
 	Set imagespace modifiers enable: 0-Off, 1-On
420	 	SetUFOCamSpeedMult (sucsm)	Float
 	Set speed mult for free camera.
421	 	ToggleTestLight (TTL)	Integer, Float, Float
 	Toggle test light (radius, magnitude).
422	 	PlaceLocationMarker (plm)	Float:X, Float:Y, Float:Z, String:Name, Integer:Color
 	PlaceLocationMarker x y z (optional name) (optional color )
423	 	ClearLocationMarkers (clm)	 
424	 	ToggleMultiboundCheck (mbc)	 
 	Toggle all Multibound check functionality
425	 	ToggleGrabIK (tgik)	 
 	Toggles Grab IK system
426	 	ToggleLookIK (tlik)	 
 	Toggles Look IK system
427	 	ToggleRagdollAnim (tra)	 
 	Toggles Ragdoll Anim system
428	 	AddOcclusionPlane (AOP)	Float, Float
 	Add Occlusion plane (x size, y size).
429	 	SetGlobalRadialBlur (sgrb)	Float, Float, Float, Float, Float
 	Set parameters for the global radial blur
430	 	OutputTextureUseMap (OTUM)	String
 	Writes out texture use map data to file.
431	 	ClearScreenBlood (csb)	 
 	Clear screen blood
432	 	ResetDialogueFlags (rdf)	 
 	Debug resets SayOnce, TalkedToPc, etc.
433	ref.	GetActorValueInfo (GetAVInfo)	UNK:5:Actor Value
 	Get detailed an actor value info. [player.getav luck]
434	 	ToggleBoundVisGeom (tbvg)	 
 	Toggles bound visualization for a ref
435	 	SetConsoleOuputFile (scof)	String
 	Sets the given file as target for console output.
436	 	IgnoreRenderPass (irp)	Integer, Integer
 	Ignore the given renderpass
437	 	PlayVATSCameras (pvc)	String
 	Play this set of VATS cameras for the next VATS playback.
438	 	SetTargetFalloff (stfo)	Float, Float, Float, Float
 	Sets the falloff of the target
439	 	ToggleActorMover (tam)	 
 	Toggles actor movement through pathing system
440	 	CopySaves (CopySaves)	String
 	Copy save games from the host machine or a memory stick (ms).
441	 	DrawSkeleton (das)	Integer, Integer, Integer
 	Enables skeleton drawing
442	 	ToggleEOFImageSpace (teofis)	 
 	Toggles end of frame imagespace effects
443	 	ForceFileCache (ffc)	 
 	Enumerates Miles samples and streams
444	 	DumpNiUpdates (dnu)	 
 	Dumps the next frame's calls to NiNode/NiAVObject::UpdateDownwardPass to NiDump*.xls (only Debug/PIX 360 builds)
445	 	TestDegrade	Integer
 	degrades the specified number of MB of textures. negative values degrade all possible. (only 360 builds)
446	 	ToggleDepthBias (tdb)	 
 	toggles depth bias for decals on or off
447	 	ToggleSPURenderBatch (tSPURB)	 
 	toggles the SPU render batch on or off
448	 	ToggleSPUTransformUpdate (tSPUTU)	 
 	toggles the SPU transform update on or off
449	 	ToggleSPUCulling (tSPUC)	 
 	toggles the SPU culling on or off
450	 	ModifyFaceGen (mfg)	String, Integer, Integer
 	Modify FaceGen Functionality
451	 	ForceRSXCrash (fRSX)	 
 	Forces an RSX crash
452	 	SetEmitterParticleMax	Integer
 	Sets the maximum number of particles emitted by a non-master particle system emitter
453	 	SetMPSParticleMax	Integer
 	Sets the maximum number of particles emitted by master particle system emitter set
454	 	SetTaskThreadSleep	Integer
 	Sets the task thread sleep value (-1 to disable).
455	 	SetTaskThreadUpdateSleep	Integer
 	Sets the task thread update sleep value (-1 to disable).
456	 	EvalActorTextures (EAT)	 
457	 	CreateSaveData (createsaves)	Integer, Integer
 	Creates save data.
458	 	CreateGameData	String, Integer
 	Creates game data.
459	 	LoadFlashMovie	String
 	Loads a Flash movie for the GUI
460	 	ShowMenu	String
 	Show or create a menu
461	 	HideMenu	String
 	Hide or close a menu
462	ref.	RecvAnimEvent (rae)	String, String
 	Simulate recieving an animation event from an animation graph.
463	 	RunConsoleBatch (bat)	String
 	Run a console batch file
464	 	LTGraph (LTGraph)	String
 	Sets the input-to-player movement graph
465	 	RTGraph (RTGraph)	String
 	Sets the input-to-camera movement graph
466	 	ToggleHeapTracking (THT)	 
 	Toggle Heap Tracking
467	 	TogglePoolTracking (TPT)	 
 	Toggle Pool Tracking
468	 	RumbleManager (RM)	Float, Float, Float, Float
 	RumbleManager
469	 	InstallMemoryTracker (IMT)	String
 	Install Memory Tracker
470	 	UninstallMemoryTracker (UMT)	 
 	Uninstall Memory Tracker
471	 	CheckMemory (chkmem)	 
 	Check Memory
472	 	SetStackDepth (SSD)	Integer
 	Set Stack Depth
473	ref.	PathToRef (PTR)	UNK:4:TESObjectREFR
 	Force actor to path to reference.
474	 	StartAIControlledRobotTest (SPTP)	String, String, String
 	Starts player pathing memory test.
475	 	TestHandleManagerWarnAndKillSDM (THMWAKS)	 
 	Purely for testing: lists every un-destroyed handle, then destroys it.
476	ref.	SetFaceTarget (SFT)	UNK:4:TESObjectREFR
 	Force actor to face a reference.
477	 	PrecomputedLOSGeneration (plg)	String, String, String
478	 	PrecomputedLOSDebug (pld)	String, String, String
479	ref.	SetPathSprinting (SPS)	Integer
 	Force actor to sprint.
480	ref.	SetAnimGraphVar (sgv)	String, String, String, String, String
 	Set an animation graph variable.
481	 	SetTreeTrunkFlexibility (STSTF)	Float
 	Set the flexibility of trees trunk.
482	 	SetTreeBranchFlexibility (STSBF)	Float
 	Set the flexibility of trees branches.
483	 	SetTreeBranchAnimationRange (STSBR)	Integer, Integer
 	Restrict the range of tree branch animations.
484	 	SetTreeLeafFlexibility (STSLF)	Float
 	Set the flexibility of trees leaves.
485	 	SetTreeLeafAmplitude (STSLAMP)	Float
 	Set the amplitude of trees leaves.
486	 	SetTreeLeafFrequency (STSLFRQ)	Float
 	Set the frequency of trees leaves.
487	 	SetTreeWindDirection (STSWD)	Float, Float
 	Set the tree system wind direction.
488	 	SetTreeWindMagnitude (STSWM)	Float
 	Set the tree system wind magnitude.
489	 	SetBloodParam (SBP)	String, Float
 	Set blood parameters.
490	 	RegisterPrefix (RPRFX)	String
 	Register all resources at a path prefix.
491	 	ToggleEventLog (TEL)	 
 	Show a log of misc. events for the selected debug REFR.
492	 	ExportPerfTrackingData (EPTD)	 
 	Export performance monitor tracking data to a file.
493	 	DisplayGraphVariable (dgv)	String
 	Gives control of variable filters used on the animation debug text page.
494	ref.	ToggleMotionDriven (TMD)	Integer
 	Toggles motion driven animation
495	ref.	ToggleControlsDriven (TC)	Integer
 	Toggles controls driven character
496	 	ToggleGUIOverlay (TGO)	 
 	Toggles the graphical overlay
497	 	ModifyGUIOverlay (MGO)	String, String, Integer, Integer
 	Modifies a graphical overlay
498	ref.	SetActionComplete (SAC)	 
 	Sets all active actions on actor in scene complete
499	 	SetConsoleScopeQuest (SSQ)	Quest
 	Sets the scope quest for all console functions. No param clears current scope quest.
500	 	GetDistanceFromActorsPath (GDFAP)	UNK:4:TESObjectREFR
 	Prints out the distance from a reference to an actor's path.
501	 	ClearConsole (clear)	 
 	Clears the console log
502	 	ToggleMarkers (TMK)	 
 	Toggle markers
503	 	KillAllProjectiles (kap)	 
 	Kill all projectiles
504	 	PlayTerrainEffect (PTFX)	String
 	Plays a terrain effect at target REFR
505	 	CellInfo	 
 	Toggle Cell Info
506	 	Textures (tx)	 
 	Toggle Texture Info
507	 	Timing (FPS)	 
 	Toggle Timing Info
508	 	Polygons (Polys)	 
 	Toggle Mesh Render Info
509	 	Actors (Actors)	 
 	Toggle Actor Info
510	 	Quest (Quest)	 
 	Toggle Quest Event Info
511	 	Particles (Particles)	 
 	Toggle Particle Info
512	 	Memory	 
 	Toggle Memory Info
513	ref.	ShowNodes	String
 	Show nodes- [All], [Off]
514	 	SaveDebugTextPages (SaveDTP)	 
 	Save Debug Text Pages settings to .ini file.
515	 	OutputFixedStringTable (OFST)	 
 	Output the fixed string table to a file.
516	 	AttachPapyrusScript (APS)	String, String
 	Attaches a papyrus script to a reference
517	 	SaveHavokSnapshot (SHS)	String, Integer
 	Saves a Havok snapshot of the hkpWorld the player is currently in
518	ref.	SetNPCWeight (SNPCW)	Float
 	[0-100]: Sets the weight of an NPC and reloads his 3D to visualize the weight change
519	 	ShowClosestLocationForSphere (SCLFS)	Float, Float, Integer:Int
 	Finds the closest location where a sphere can fit in the navmesh, and displays it
520	 	PrintShaderMacros (PSM)	String, String
 	Given a shader and technique ID, prints the macros used to compile its shader.
521	 	ToggleAudioOverlay (TAO)	 
 	Toggle the Audio Debug Overlay.
522	 	TogglePapyrusLog (TPLog)	 
 	Toggle the Papyrus Log Overlay.
523	 	ToggleContextOverlay (TCO)	 
 	Toggle the Context Overlay.
524	 	ForceOutOfMemory (FOOM)	 
 	Forces an OOM state.
525	 	ToggleMovement (TMOVE)	 
 	Toggles all actor movement
526	 	ToggleAnimations (TANIM)	 
 	Toggles all actor animations
527	 	SetWarning	String, Integer
 	Params: Name [0/1]. 0 is default for 2nd param.
528	 	TestFadeNodes (TestFN)	 
529	 	ShowLowMaxHeights (slmh)	Integer, Integer
530	 	ShowHighMaxHeights (shmh)	Integer
531	 	EnableStoryManagerLogging (esml)	String
 	Enable story manager logging.
532	 	SetFramebufferRange (sfbr)	Float
 	Set frambuffer range
533	 	DumpPapyrusStacks (dps)	 
 	Dumps all Papyrus stack information to the log
534	 	DumpPapyrusUpdates (dpu)	 
 	Dumps all Papyrus update registrations to the log
535	ref.	DisplayLookIKDebug (dlik)	 
536	 	SoundCatMod (scm)	UNK:79:SoundCategory, String, Float
 	In-game control of sound categories.
537	 	RunCompaction (rcmpct)	 
 	Run a pass of compaction if possible.
538	 	ToggleAnimatorCam (animcam)	 
 	Toggles a 3rd-person camera mode designed for animators' ease-of-use
539	 	ToggleImmortalMode (TIM)	 
 	Toggle Immortal mode
540	 	SetSubgraphToDebug (sgd)	Integer
 	Subgraph To Debug
541	 	EnableRumble	Integer
542	 	HavokVDBCapture (vdbcapture)	String, Integer
 	Havok VDB Capture
543	 	ToggleMapCam (tmc)	 
 	toggle a view mode similar to the map camera for debugging
544	 	ToggleNavmeshInfo (tni)	 
 	toggle a view mode similar to the map camera and displays navmesh infos
545	ref.	PlaySyncAnim (syncanim)	String, UNK:4:ObjectReferenceID
 	Play Sync Anim
546	 	SetFormKnown	String, Integer
 	Sets the known flag on a form
547	 	SetDebugQuest	Quest, Integer, Integer:State
 	Sets the quest to be the only one startable for its event type.
548	 	SetQuestAliasLogging (sqal)	Quest, Integer
 	Turns alias logging on/off for a quest.
549	 	SetRace	String
 	Sets the passed in actor's race.
550	 	FindForm (find)	String, String
 	Find a form
551	 	StartPapyrusScriptProfile (StartPSP)	String
 	Starts profiling a Papyrus script.
552	 	StopPapyrusScriptProfile (StopPSP)	String
 	Stops profiling a Papyrus script.
553	 	StartPapyrusFormProfile (StartPFP)	UNK:62:Form
 	Starts profiling Papyrus scripts on a form.
554	 	StopPapyrusFormProfile (StopPFP)	UNK:62:Form
 	Stops profiling Papyrus scripts on a form.
555	 	PrintQuestSceneInfo (PrintS)	Quest
 	Prints to the Quest Inf file the current state of scenes.
556	ref.	IsInvulnerable (isinv)	 
 	Is the actor invulnerable?
557	 	ExportInventoryItemInfo (eiii)	Integer
558	 	CollisionMesh (colmesh)	 
 	Toggle Mesh Collision Info
559	 	IsolateRendering (ir)	 
560	 	ToggleWaterCurrentGeometry (twcg)	Integer
561	ref.	PerformAction (pa)	UNK:83:Action
562	 	StartTrackPlayerDoors (StartTPD)	 
563	 	StopTrackPlayerDoors (StopTPD)	 
564	 	CheckPlayerDoors (CheckPD)	Integer
565	 	ToggleSPUMovement (tSPUM)	 
 	toggles the SPU movement on or off
566	 	SetInChargen	Integer, Integer, Integer
567	 	ClearAchievement	Integer
Functions
ID		Name	Arguments
4096	ref.	GetWantBlocking	 
4097	ref.	GetDistance	UNK:4:ObjectReferenceID
4098	ref.	AddItem	UNK:53:ObjectID, Integer:Count, Integer:Flag
4099	 	SetEssential	UNK:25:Actor Base, Integer
4100	ref.	Rotate	UNK:8:Axis, Integer:Speed
4101	ref.	GetLocked	 
4102	ref.	GetPos	UNK:8:Axis
4103	ref.	SetPos	UNK:8:Axis, Float
4104	ref.	GetAngle	UNK:8:Axis
4105	ref.	SetAngle	UNK:8:Axis, Float
4106	ref.	GetStartingPos	UNK:8:Axis
4107	ref.	GetStartingAngle	UNK:8:Axis
4108	 	GetSecondsPassed	 
4109	ref.	Activate	UNK:4:ObjectReferenceID, Integer
4110	ref.	GetActorValue (GetAV)	UNK:5:Actor Value
 	Get an actor value. [player.getav luck]
4111	ref.	SetActorValue (SetAV)	UNK:5:Actor Value, Float:Amount
 	Set an actor value. [player.setav luck, 75]
4112	ref.	ModActorValue (ModAV)	UNK:5:Actor Value, Float:Amount
 	Modify an actor value. [player.modav luck, -10]
4113	ref.	SetAtStart	 
4114	 	GetCurrentTime	 
4115	ref.	PlayGroup	UNK:10:Animation Group, Integer:Flags
 	IE: PlayGroup SEQNAME 1
4116	ref.	LoopGroup	UNK:10:Animation Group, Integer, Integer:Flags
4117	ref.	SkipAnim	 
4118	ref.	StartCombat	Actor
4119	ref.	StopCombat	 
4120	ref.	GetScale	 
4121	ref.	IsMoving	 
 	Is the actor moving?
4122	ref.	IsTurning	 
 	Is the actor turning?
4123	ref.	GetLineOfSight (GetLOS)	UNK:4:ObjectReferenceID
4124	ref.	AddSpell	UNK:7:Spell Item
4125	ref.	RemoveSpell	UNK:7:Spell Item
4126	ref.	Cast	UNK:11:Magic Item, UNK:4:ObjectReferenceID, UNK:68:Source
4127	 	GetButtonPressed	 
4128	ref.	GetInSameCell	UNK:4:ObjectReferenceID
4129	ref.	Enable	Integer
4130	ref.	Disable	Integer
4131	ref.	GetDisabled	 
4132	 	MenuMode	Integer
4133	ref.	PlaceAtMe	UNK:21:ObjectID, Integer:Count, Integer:Distance, Integer:Direction
4134	 	PlaySound	UNK:12:Sound, Integer
 	Play a 2D sound. (Optional: 1 to indicate a system sound, 0 otherwise.
4135	ref.	GetDisease	 
4136	 	FailAllObjectives	Quest
 	Fail all of a quest's objectives
4137	ref.	GetClothingValue	 
4138	ref.	SameFaction	Actor
4139	ref.	SameRace	Actor
4140	ref.	SameSex	Actor
4141	ref.	GetDetected	Actor
4142	ref.	GetDead	 
4143	ref.	GetItemCount	UNK:53:ObjectID
4144	ref.	GetGold	 
4145	ref.	GetSleeping	 
4146	ref.	GetTalkedToPC	 
4147	ref.	Say	Topic, Integer, UNK:25:Actor Base, Integer, Integer
4148	ref.	SayTo	Actor, Topic, Integer, Integer
4149	 	GetScriptVariable	UNK:4:ObjectReferenceID, UNK:22:Variable Name
4150	 	StartQuest	Quest
4151	 	StopQuest	Quest
4152	 	GetQuestRunning (GetQR)	Quest
4153	 	SetStage	Quest, UNK:23:Stage
4154	 	GetStage	Quest
4155	 	GetStageDone	Quest, UNK:23:Stage
4156	ref.	GetFactionRankDifference	UNK:17:Faction, Actor
4157	ref.	GetAlarmed	 
4158	 	IsRaining	 
4159	ref.	GetAttacked	 
4160	ref.	GetIsCreature	 
4161	ref.	GetLockLevel	 
4162	ref.	GetShouldAttack	Actor
4163	ref.	GetInCell	UNK:9:Cell
4164	ref.	GetIsClass	UNK:16:Class
4165	ref.	GetIsRace	UNK:15:Race
4166	ref.	GetIsSex	UNK:18:Sex
4167	ref.	GetInFaction	UNK:17:Faction
4168	ref.	GetIsID	UNK:21:ObjectID
4169	ref.	GetFactionRank	UNK:17:Faction
4170	 	GetGlobalValue	UNK:19:Global
4171	 	IsSnowing	 
4172	 	FastTravel (ft)	UNK:4:TESObjectREFR
 	Player Fast Travel to Marker
4173	 	GetRandomPercent	 
4174	 	RemoveMusic	UNK:57:Music
4175	 	GetQuestVariable	Quest, UNK:22:Variable Name
4176	ref.	GetLevel	 
4177	ref.	IsRotating	 
4178	ref.	RemoveItem	UNK:53:ObjectID, Integer:Count, Integer:Flag
4179	ref.	GetLeveledEncounterValue	Integer
4180	 	GetDeadCount	UNK:25:Actor Base
4181	 	AddToMap (ShowMap)	UNK:24:Map Marker, Integer
4182	ref.	StartConversation	UNK:4:TESObjectREFR, Topic, UNK:4:TESObjectREFR, UNK:4:TESObjectREFR, Integer, Integer
 	Start a conversation [Bob.StartConversation Joe, SecretTopic]
4183	ref.	Drop	UNK:3:ObjectID, Integer:Count
4184	 	AddTopic	Topic
4185	 	ShowMessage	UNK:52:Message
4186	ref.	SetAlert	Integer
4187	ref.	GetIsAlerted	 
4188	ref.	Look	UNK:4:ObjectReferenceID, Integer
4189	ref.	StopLook	 
4190	ref.	EvaluatePackage (evp)	 
4191	 	SendAssaultAlarm	Actor, UNK:17:Faction
4192	 	EnablePlayerControls (epc)	Integer, Integer, Integer, Integer, Integer, Integer, Integer, Integer, Integer
 	9 optional (0/1) values. movement|fighting|pov|looking|sneaking|menu|activate|journal|POV Type ex: 1 0 0 1 0 0 0 0 0
4193	 	DisablePlayerControls (dpc)	Integer, Integer, Integer, Integer, Integer, Integer, Integer, Integer, Integer
 	9 optional (0/1) values. movement|fighting|pov|looking|sneaking|menu|activate|journal|POV Type ex: 1 0 0 1 0 0 0 0 0
4194	 	GetPlayerControlsDisabled (gpc)	Integer, Integer, Integer, Integer, Integer, Integer, Integer, Integer
 	8 optional (0/1) values. movement|fighting|pov|looking|sneaking|menu|activate|journal ex: 1 0 0 1 0 0 0 0
4195	ref.	GetHeadingAngle	UNK:4:ObjectReferenceID
4196	ref.	PickIdle	 
4197	ref.	IsWeaponMagicOut	 
4198	ref.	IsTorchOut	 
4199	ref.	IsShieldOut	 
4200	ref.	CreateDetectionEvent	UNK:4:TESObjectREFR, Integer:Integer
4201	ref.	IsActionRef	UNK:4:ObjectReferenceID
4202	ref.	IsFacingUp	 
4203	ref.	GetKnockedState	 
4204	ref.	GetWeaponAnimType	 
4205	ref.	IsWeaponSkillType	UNK:5:Actor Value
4206	ref.	GetCurrentAIPackage	 
4207	ref.	IsWaiting	 
4208	ref.	IsIdlePlaying	 
4209	 	CompleteQuest	Quest
4210	ref.	Lock	Integer, Integer
4211	ref.	UnLock	Integer
4212	ref.	IsIntimidatedbyPlayer	 
4213	 	IsPlayerInRegion	UNK:82:Region
4214	ref.	GetActorAggroRadiusViolated	 
4215	ref.	GetCrimeKnown	UNK:28:Crime Type, Actor:Criminal, Actor:Victim
4216	 	SetEnemy	UNK:17:Faction, UNK:17:Faction, Integer:Value, Integer:Value
4217	 	SetAlly	UNK:17:Faction, UNK:17:Faction, Integer:Value, Integer:Value
4218	ref.	GetCrime	Actor, UNK:28:Crime Type
4219	ref.	IsGreetingPlayer	 
4220	ref.	StartMisterSandMan	Actor
4221	ref.	IsGuard	 
4222	ref.	StartCannibal	Actor
4223	ref.	HasBeenEaten	 
4224	ref.	GetStaminaPercentage (GetStamina)	 
4225	 	GetPCIsClass	UNK:16:Class
4226	 	GetPCIsRace	UNK:15:Race
4227	 	GetPCIsSex	UNK:18:Sex
4228	 	GetPCInFaction	UNK:17:Faction
4229	ref.	SameFactionAsPC	 
4230	ref.	SameRaceAsPC	 
4231	ref.	SameSexAsPC	 
4232	ref.	GetIsReference	UNK:4:ObjectReferenceID
4233	ref.	SetFactionRank	UNK:17:Faction, Integer:Value
4234	ref.	ModFactionRank	UNK:17:Faction, Integer:Value
4235	ref.	KillActor (kill)	Actor, Integer, Integer
4236	ref.	ResurrectActor (resurrect)	Integer
4237	ref.	IsTalking	 
4238	ref.	GetWalkSpeed (GetWalk)	 
4239	ref.	GetCurrentAIProcedure	 
4240	ref.	GetTrespassWarningLevel	 
4241	ref.	IsTrespassing	 
4242	ref.	IsInMyOwnedCell	 
4243	 	GetWindSpeed	 
4244	 	GetCurrentWeatherPercent (getweatherpct)	 
4245	 	GetIsCurrentWeather (getweather)	UNK:33:Weather ID
4246	ref.	IsContinuingPackagePCNear	 
4247	ref.	SetCrimeFaction	UNK:17:Faction
4248	ref.	GetIsCrimeFaction	UNK:17:Faction
4249	ref.	CanHaveFlames	 
4250	ref.	HasFlames	 
4251	ref.	AddFlames	 
4252	ref.	RemoveFlames	 
4253	ref.	GetOpenState	 
4254	ref.	MoveToMarker (MoveTo)	UNK:4:ObjectReferenceID, Float, Float, Float
4255	ref.	GetSitting	 
4256	ref.	GetFurnitureMarkerID	 
4257	ref.	GetIsCurrentPackage	UNK:29:Package
4258	ref.	IsCurrentFurnitureRef	UNK:4:ObjectReferenceID
4259	ref.	IsCurrentFurnitureObj	UNK:20:Furniture
4260	ref.	SetSize (CSize)	Float
 	Shrink/Grow an actor. [player.shrink 0.0 or radius]
4261	ref.	RemoveMe	Container
4262	ref.	DropMe	 
4263	 	GetFactionReaction	UNK:17:Faction, UNK:17:Faction
4264	 	SetFactionReaction	UNK:17:Faction, UNK:17:Faction, Integer:Value
4265	 	ModFactionReaction	UNK:17:Faction, UNK:17:Faction, Integer:Value
4266	 	GetDayOfWeek	 
4267	ref.	IgnoreCrime	Integer
4268	 	GetTalkedToPCParam	Actor
4269	ref.	RemoveAllItems	Container, Integer:Ownership Added, Integer:No Message Flag
4270	 	WakeUpPC	Integer
4271	 	IsPCSleeping	 
4272	 	IsPCAMurderer	 
4273	ref.	SetCombatStyle (setcs)	UNK:30:Combat Style
4274	 	PlaySound3D	UNK:12:Sound
4275	 	SelectPlayerSpell (spspell)	UNK:7:Spell Item, UNK:68:Casting Source
4276	ref.	HasSameEditorLocAsRef	UNK:4:TESObjectREFR, UNK:59:Keyword
4277	ref.	HasSameEditorLocAsRefAlias	UNK:63:QuestAlias, UNK:59:Keyword
4278	ref.	GetEquipped	UNK:53:ObjectID
4279	ref.	Wait	UNK:29:Package
4280	ref.	StopWaiting	UNK:29:Package
4281	ref.	IsSwimming	 
4282	 	ScriptEffectElapsedSeconds	 
4283	 	SetCellPublicFlag (setpublic)	UNK:9:Cell, Integer:Flag
 	Set public flag on cell. [setpublic MyCell 1]
4284	 	GetPCSleepHours	 
4285	 	SetPCSleepHours	Integer
4286	 	GetAmountSoldStolen	 
4287	 	ModAmountSoldStolen	Integer
4288	 	GetIgnoreCrime	 
4289	 	GetPCExpelled	UNK:17:Faction
4290	 	SetPCExpelled	UNK:17:Faction, Integer:Value
4291	 	GetPCFactionMurder	UNK:17:Faction
4292	 	SetPCFactionMurder	UNK:17:Faction, Integer:Value
4293	 	GetPCEnemyofFaction	UNK:17:Faction
4294	 	SetPCEnemyofFaction	UNK:17:Faction, Integer:Value
4295	 	GetPCFactionAttack	UNK:17:Faction
4296	 	SetPCFactionAttack	UNK:17:Faction, Integer:Value
4297	 	StartScene	UNK:67:Scene
4298	 	StopScene	UNK:67:Scene
4299	ref.	GetDestroyed	 
4300	ref.	SetDestroyed	Integer
4301	ref.	GetActionRef (getAR)	 
4302	ref.	GetSelf (this)	 
4303	ref.	GetContainer	 
4304	ref.	GetForceRun	 
4305	ref.	SetForceRun	Integer
4306	ref.	GetForceSneak	 
4307	ref.	SetForceSneak	Integer
4308	 	AdvancePCSkill (AdvSkill)	UNK:5:Actor Value, Float:Amount
 	Give the player the given amount of skill usage [advskill onehanded 100]
4309	 	AdvancePCLevel (AdvLevel)	 
4310	ref.	HasMagicEffect	UNK:31:Magic Effect
4311	ref.	GetDefaultOpen	 
 	Returns 1 if this object is open by default.
4312	ref.	SetDefaultOpen	Integer
 	A value of 1 will make the object open by default.
4313	 	ShowClassMenu	 
4314	 	ShowRaceMenu (SetPlayerRace)	UNK:15:Race
 	ShowRaceMenu no parameter will bring up the race menu. SetPlayerRace raceID will change the players race to raceID and reload the players geometry.
4315	ref.	GetAnimAction	 
 	Return the actors current anim action.
4316	 	ShowNameMenu	 
4317	ref.	SetOpenState	Integer
 	A value of 1 will make the object open.
4318	ref.	ResetReference (RecycleActor)	UNK:4:ObjectReferenceID
 	Optional destination reference.
4319	ref.	IsSpellTarget	UNK:11:Magic Item
4320	 	GetVATSMode	 
4321	 	GetPersuasionNumber	 
4322	ref.	GetVampireFeed	 
4323	ref.	GetCannibal	 
4324	 	GetIsClassDefault	UNK:16:Class
4325	 	GetClassDefaultMatch	 
4326	 	GetInCellParam	UNK:9:Cell, UNK:4:ObjectReferenceID
4327	 	UnusedFunction1	 
4328	ref.	GetCombatTarget	 
4329	ref.	GetPackageTarget	 
4330	 	ShowSpellMaking	 
4331	 	GetVatsTargetHeight	 
4332	ref.	SetGhost	Integer
 	A value of 1 will make the NPC act as a 'ghost'.
4333	ref.	GetIsGhost	 
 	Returns 1 if the actor is acting as a 'ghost'.
4334	ref.	EquipItem (EquipObject)	UNK:3:ObjectID, Integer:Flag, UNK:68:Hand
4335	ref.	UnequipItem (UnEquipObject)	UNK:3:ObjectID, Integer:Flag, UNK:68:Hand
4336	ref.	SetClass	UNK:16:Class
4337	 	SetUnconscious	Integer
 	A value of 1 will be unconscious.
4338	ref.	GetUnconscious	 
 	Returns 1 if is unconscious.
4339	 	SetRestrained	Integer
 	A value of 1 will be restrained.
4340	ref.	GetRestrained	 
 	Returns 1 if is restrained.
4341	ref.	ForceFlee (Flee)	UNK:9:Cell, UNK:4:ObjectReferenceID
 	Forces a actor to flee if destination or cell is passed in uses them
4342	 	GetIsUsedItem	UNK:56:ObjectID
4343	 	GetIsUsedItemType	UNK:32:Form Type
4344	 	IsScenePlaying	UNK:67:Scene
4345	ref.	IsInDialogueWithPlayer	 
 	True if actor is currently in a dialogue state with player
4346	 	GetLocationCleared	UNK:61:Location
4347	 	SetLocationCleared	UNK:61:Location, Integer
4348	ref.	ForceRefIntoAlias	UNK:63:QuestAlias
4349	 	EmptyRefAlias	UNK:63:QuestAlias
4350	ref.	GetIsPlayableRace	 
4351	ref.	GetOffersServicesNow	 
4352	 	GetGameSetting (GetGS)	String
4353	ref.	StopCombatAlarmOnActor (SCAOnActor)	 
4354	ref.	HasAssociationType	Actor, UNK:69:AssociationType
4355	ref.	HasFamilyRelationship (Family)	Actor
4356	 	SetWeather (sw)	UNK:33:Weather ID, Integer
 	Sets the active weather to the specified type
4357	ref.	HasParentRelationship (IsParent)	Actor
4358	ref.	IsWarningAbout	UNK:37:Form List
4359	ref.	IsWeaponOut	 
4360	ref.	HasSpell	UNK:7:Spell Item
4361	 	IsTimePassing	 
4362	 	IsPleasant	 
4363	 	IsCloudy	 
4364	 	TrapUpdate	 
4365	 	ShowQuestObjectives (SQO)	Quest
 	Shows the list of current quest objectives
4366	ref.	ForceActorValue (ForceAV)	UNK:5:Actor Value, Float:Amount
 	Does modav XXX (value - getav XXX). [player.forceav luck 25]
4367	 	IncrementPCSkill (IncPCS)	UNK:5:Actor Value
 	Increase the player's skill by one point. [IncrementSkill onehanded]
4368	ref.	DoTrap	UNK:4:Trap, Float:Damage, Float:Lev. Damage, Float:Pushback, Float:Death Pushback, Float:Min. Velocity, Integer:Continuous, Float:Stagger
4369	 	EnableFastTravel (EnableFast)	Integer
4370	ref.	IsSmallBump	 
4371	ref.	GetParentRef	 
4372	 	PlayBink	String, Integer, Integer, Integer, Integer
4373	ref.	GetBaseActorValue (GetBaseAV)	UNK:5:Actor Value
 	Get a base actor value. [player.getbaseav luck]
4374	ref.	IsOwner	UNK:35:Owner
 	get the ownership of the ref
4375	ref.	SetOwnership	UNK:35:Owner
4376	ref.	IsCellOwner	UNK:9:Cell, UNK:35:Owner
 	get the ownership of the ref
4377	 	SetCellOwnership	UNK:9:Cell, UNK:35:Owner
4378	ref.	IsHorseStolen	 
4379	 	SetCellFullName	UNK:9:Cell, UNK:52:Message
4380	ref.	SetActorFullName	UNK:52:Message
4381	ref.	IsLeftUp	 
4382	ref.	IsSneaking	 
4383	ref.	IsRunning	 
4384	ref.	GetFriendHit	 
4385	ref.	IsInCombat	Integer
4386	ref.	SetPackDuration (SPDur)	Float
4387	ref.	PlayMagicShaderVisuals (PMS)	UNK:36:Effect Shader ID, Float:Time
4388	ref.	PlayMagicEffectVisuals (PME)	UNK:31:Magic Effect, Float:Time
4389	ref.	StopMagicShaderVisuals (SMS)	UNK:36:Effect Shader ID
4390	ref.	StopMagicEffectVisuals (SME)	UNK:31:Magic Effect
4391	 	ResetInterior	UNK:9:Cell
4392	ref.	IsAnimPlaying	UNK:10:Animation Group
4393	ref.	SetActorAlpha (SAA)	Float, Integer:Int
4394	ref.	EnableLinkedPathPoints	 
4395	ref.	DisableLinkedPathPoints	 
4396	ref.	IsInInterior	 
4397	 	ForceWeather (fw)	UNK:33:Weather ID, Integer
 	Forces the active weather to the specified type, without transition.
4398	ref.	ToggleActorsAI	 
4399	ref.	IsActorsAIOff	 
4400	ref.	IsWaterObject	 
 	Water condition used for drinking animation.
4401	ref.	GetPlayerAction	 
4402	ref.	IsActorUsingATorch	 
4403	ref.	SetLevel	Integer, Integer, Integer, Integer
4404	ref.	ResetFallDamageTimer	 
4405	 	IsXBox	 
4406	ref.	GetInWorldspace	UNK:27:WorldSpace
4407	 	ModPCMiscStat (ModPCMS)	UNK:41:Miscellaneous Stat, Integer
4408	 	GetPCMiscStat (GetPCMS)	UNK:41:Miscellaneous Stat
4409	ref.	GetPairedAnimation (GPA)	 
 	See if this reference is currently playing a paired animation.
4410	ref.	IsActorAVictim	 
4411	 	GetTotalPersuasionNumber	 
4412	ref.	SetScale	Float
 	Set the scale of an object
4413	ref.	ModScale	Float
 	Modify the scale of an object
4414	 	GetIdleDoneOnce	 
4415	 	KillAllActors (killall)	Actor
4416	ref.	GetNoRumors	 
4417	ref.	SetNoRumors	Integer
4418	ref.	Dispel	UNK:11:Magic Item
4419	ref.	GetCombatState	 
4420	 	TriggerHitShader (ths)	Float
4421	ref.	GetWithinPackageLocation	UNK:78:PackageData (Location)
4422	 	Reset3DState	 
4423	ref.	IsRidingHorse	 
4424	ref.	DispelAllSpells	 
4425	ref.	IsFleeing	 
4426	 	AddAchievement	Integer
4427	ref.	DuplicateAllItems	Container, Integer:Ownership Added
4428	ref.	IsInDangerousWater	 
4429	 	EssentialDeathReload	String
4430	 	SetShowQuestItems	Integer
4431	ref.	DuplicateNPCStats	Actor
4432	ref.	ResetHealth	 
4433	ref.	SetIgnoreFriendlyHits (sifh)	Integer
 	Set whether an actor should ignore friendly hits (0-no, 1-yes)
4434	ref.	GetIgnoreFriendlyHits (gifh)	 
 	See if an actor is ignoring friendly hits
4435	ref.	IsPlayersLastRiddenHorse	 
4436	ref.	SetActorRefraction (sar)	Float
4437	ref.	SetItemValue	Integer
4438	 	SetRigidBodyMass	Float
4439	ref.	ShowViewerStrings (svs)	 
4440	 	ReleaseWeatherOverride (rwo)	 
4441	 	SetAllReachable	Integer
4442	 	SetAllVisible	Integer
4443	 	SetNoAvoidance	Integer
4444	ref.	SendTrespassAlarm	Actor
4445	 	SetSceneIsComplex	Integer
4446	 	Autosave	 
4447	 	StartMasterFileSeekData	 
4448	 	DumpMasterFileSeekData	 
4449	ref.	IsActor	 
4450	ref.	IsEssential	 
4451	ref.	PreloadMagicEffect	UNK:31:Magic Effect
4452	 	ShowDialogSubtitles	Integer
4453	ref.	SetPlayerResistingArrest	 
4454	 	IsPlayerMovingIntoNewSpace	 
4455	ref.	GetInCurrentLoc	UNK:61:Location
4456	ref.	GetInCurrentLocAlias	UNK:63:QuestAlias
4457	ref.	GetTimeDead	 
4458	ref.	HasLinkedRef	UNK:59:Keyword
4459	ref.	GetLinkedRef	UNK:59:Keyword
4460	ref.	DamageObject (do)	Float
4461	ref.	IsChild	 
4462	 	GetStolenItemValueNoCrime	UNK:17:Faction
4463	 	GetLastPlayerAction	 
4464	 	IsPlayerActionActive	Integer
4465	ref.	SetTalkingActivatorActor	Actor
4466	ref.	IsTalkingActivatorActor	Actor
4467	ref.	ShowBarterMenu (sbm)	Integer
4468	ref.	IsInList	UNK:37:Form List
4469	 	GetStolenItemValue	UNK:17:Faction
4470	ref.	AddPerk	UNK:39:Perk
4471	ref.	GetCrimeGoldViolent (getviolent)	UNK:17:Faction
4472	ref.	GetCrimeGoldNonviolent (getnonviolent)	UNK:17:Faction
4473	 	ShowRepairMenu (srm)	 
4474	ref.	HasShout	UNK:64:Shout
4475	 	AddNote (AN)	UNK:40:Note
4476	 	RemoveNote (RN)	UNK:40:Note
4477	 	GetHasNote (GetN)	UNK:40:Note
4478	ref.	AddToFaction (Addfac)	UNK:17:Faction, Integer:Value
4479	ref.	RemoveFromFaction (Removefac)	UNK:17:Faction
4480	ref.	DamageActorValue (DamageAV)	UNK:5:Actor Value, Float:Amount
 	Damage an actor value.
4481	ref.	RestoreActorValue (RestoreAV)	UNK:5:Actor Value, Float:Amount
 	Restore an actor value.
4482	 	TriggerHUDShudder (hudsh)	Float, Float, Float, Float
 	Trigger shudder effect on HUD. Params=Intensity/Duration.
4483	 	GetObjectiveFailed	Quest, Integer
4484	 	SetObjectiveFailed	Quest, Integer, Integer:State
4485	 	SetGlobalTimeMultiplier (sgtm)	Float
4486	ref.	GetHitLocation	 
 	What BGSBodyPart::LIMB_ENUM was hit. -1 = none, 0 = torso, ...
4487	 	IsPC1stPerson (pc1st)	 
 	Is the player playing in 1st person mode?
4488	 	PurgeCellBuffers (pcb)	 
 	Forcibly unloads all unattached cells in cell buffers.
4489	ref.	PushActorAway	UNK:4:ObjectReferenceID, Integer
4490	ref.	SetActorsAI	Integer
4491	ref.	ClearOwnership	 
4492	ref.	GetCauseofDeath	 
 	What killed the actor
4493	ref.	IsLimbGone	Integer
 	Is BGSBodyPart::LIMB_ENUM been dismembered
4494	ref.	IsWeaponInList	UNK:37:Form List
 	Is the current weapon in the form list
4495	ref.	PlayIdle	String
 	Play this idle on the current actor.
4496	 	ApplyImageSpaceModifier (imod)	UNK:42:Imagespace Modifier ID, Float
 	Adds an imagespace modifier to the active list
4497	 	RemoveImageSpaceModifier (rimod)	UNK:42:Imagespace Modifier ID, Float
 	Removes an imagespace modifier from the active list
4498	ref.	IsBribedbyPlayer	 
4499	ref.	GetRelationshipRank	Actor
4500	ref.	SetRelationshipRank	Actor, Integer:Value
4501	 	SetCellImageSpace	UNK:9:Cell, UNK:43:ImageSpace
4502	 	ShowChargenMenu (scgm)	 
4503	 	GetVATSValue	Integer, Integer
4504	ref.	IsKiller	Actor
4505	ref.	IsKillerObject	UNK:37:Form List
4506	 	GetFactionCombatReaction	UNK:17:Faction, UNK:17:Faction
4507	ref.	UseWeapon	UNK:21:Weapon, UNK:4:Location, UNK:4:Target, Integer:NumAttacks, Integer:CrouchToReload, Integer:HoldFire, Integer:AlwaysHit, Integer:DoNoDamage, UNK:4:TargetLocation
4508	ref.	EvaluateSpellConditions (esc)	 
4509	 	ToggleMotionBlur (tmb)	 
 	Toggle Motion Blur for the selected reference
4510	ref.	Exists	UNK:4:ObjectReferenceID
4511	ref.	GetGroupMemberCount	 
4512	ref.	GetGroupTargetCount	 
4513	 	SetObjectiveCompleted	Quest, Integer, Integer:State
 	Set objective completion status: 0-Off, 1-On
4514	 	SetObjectiveDisplayed	Quest, Integer, Integer:State
 	Set objective displayed status: 0-Off, 1-On
4515	 	GetObjectiveCompleted	Quest, Integer
 	Get objective completion status: 0-Off, 1-On
4516	 	GetObjectiveDisplayed	Quest, Integer
 	Get objective displayed status: 0-Off, 1-On
4517	 	SetImageSpace	UNK:43:ImageSpace
4518	 	PipboyRadio (prad)	String, UNK:4:ObjectReferenceID
 	Control the Pipboy radio. Enable, Disable, Tune
4519	ref.	RemovePerk	UNK:39:Perk
4520	ref.	DisableAllActors (DisAA)	 
4521	ref.	GetIsFormType	String
4522	ref.	GetIsVoiceType	UNK:49:VoiceType
4523	ref.	GetPlantedExplosive	 
4524	 	CompleteAllObjectives	Quest
 	Complete all of a quest's objectives
4525	ref.	IsScenePackageRunning	 
4526	ref.	GetHealthPercentage	 
4527	 	SetAudioMultithreading (SAM)	Integer
4528	ref.	GetIsObjectType	UNK:32:Form Type
4529	 	ShowChargenMenuParams (scgmp)	Integer, Integer, Integer
4530	ref.	GetDialogueEmotion	 
4531	ref.	GetDialogueEmotionValue	 
4532	 	ExitGame (exit)	 
4533	ref.	GetIsCreatureType	Integer
4534	 	PlayerCreatePotion	UNK:31:Magic Effect, UNK:31:Magic Effect, UNK:31:Magic Effect
4535	 	PlayerEnchantObject	UNK:21:Object, UNK:31:Magic Effect, UNK:31:Magic Effect
4536	 	ShowWarning	String
4537	ref.	EnterTrigger	UNK:4:ObjectReferenceID
4538	ref.	MarkForDelete	 
4539	 	SetPlayerAIDriven	Integer
4540	ref.	GetInCurrentLocFormList	UNK:37:Form List
4541	ref.	GetInZone	UNK:50:EncounterZone
4542	ref.	GetVelocity	UNK:8:Axis
 	Gets Velocity on a reference
4543	ref.	GetGraphVariableFloat	String
4544	ref.	HasPerk	UNK:39:Perk
4545	ref.	GetFactionRelation	Actor
4546	ref.	IsLastIdlePlayed	UNK:51:Idle Form
4547	 	SetNPCRadio (snr)	Integer, UNK:4:ObjectReferenceID
 	Enable/Disable NPC radio playback: 0-Off, 1-On
4548	ref.	SetPlayerTeammate	Integer
4549	ref.	GetPlayerTeammate	 
4550	 	GetPlayerTeammateCount	 
4551	ref.	OpenActorContainer	Integer
4552	 	ClearFactionPlayerEnemyFlag	UNK:17:Faction
4553	ref.	ClearActorsFactionsPlayerEnemyFlag	 
4554	ref.	GetActorCrimePlayerEnemy	 
4555	ref.	GetCrimeGold	UNK:17:Faction
4556	ref.	SetCrimeGold	Integer:Integer , UNK:17:Faction
4557	ref.	ModCrimeGold	Integer:Integer , Integer, UNK:17:Faction
4558	 	GetPlayerGrabbedRef	 
4559	 	IsPlayerGrabbedRef	UNK:4:TESObjectREFR
4560	 	PlaceLeveledActorAtMe	UNK:21:ObjectID, Integer:LevelModifier, UNK:50:ForcedZone
4561	ref.	GetKeywordItemCount	UNK:59:Keyword
4562	 	ShowLockpickMenu (slpm)	Integer
4563	ref.	GetBroadcastState	 
4564	ref.	SetBroadcastState	Integer
4565	ref.	StartRadioConversation	Topic
4566	ref.	GetDestructionStage	 
4567	ref.	ClearDestruction	 
4568	ref.	CastImmediateOnSelf (cios)	UNK:11:Magic Item
4569	ref.	GetIsAlignment	UNK:54:Alignment
4570	 	ResetQuest	Quest
4571	 	SetQuestDelay	Quest, Float
4572	ref.	IsProtected	 
4573	ref.	GetThreatRatio	Actor
4574	ref.	MatchFaceGeometry	Actor, Integer:Value
4575	ref.	GetIsUsedItemEquipType	UNK:55:EquipType
4576	 	GetPlayerName	 
4577	ref.	FireWeapon	UNK:21:Weapon
4578	ref.	PayCrimeGold	Integer, Integer, UNK:17:Faction
4579	ref.	UnusedFunction2	Float
4580	ref.	MatchRace	Actor
4581	 	SetPCYoung	Integer
4582	 	SexChange	UNK:18:Sex, Integer
4583	ref.	IsCarryable	 
4584	ref.	GetConcussed	 
4585	 	SetZoneRespawns	UNK:50:EncounterZone, Integer
4586	ref.	SetVATSTarget	Integer
4587	ref.	GetMapMarkerVisible	 
4588	ref.	ResetInventory	 
4589	 	PlayerKnows	UNK:81:Knowable Form
4590	ref.	GetPermanentActorValue (GetPermAV)	UNK:5:Actor Value
 	Get an actor value ignoring temporary modifiers. [player.getav luck]
4591	ref.	GetKillingBlowLimb	 
 	Which BGSBodyPart::LIMB_ENUM received the killing blow
4592	ref.	GoToJail	UNK:17:Faction, Integer, Integer
4593	ref.	CanPayCrimeGold	UNK:17:Faction
4594	 	ServeTime	 
4595	ref.	GetDaysInJail	 
4596	 	EPAlchemyGetMakingPoison	 
4597	 	EPAlchemyEffectHasKeyword	UNK:59:Keyword
4598	 	ShowAllMapMarkers (tmm)	Integer, Integer, Integer
 	Shows/hides map markers (1 shows, 0 hides)(1 travel(default), 0 no travel)(1 all, 0 all but hidden(default)).
4599	ref.	GetAllowWorldInteractions	 
4600	ref.	ResetAI	 
4601	 	SetRumble	Float, Float, Float
 	Creates rumble in the controller (left motor) (right motor) (duration)
4602	 	SetNoActivationSound	UNK:12:Sound
 	Set the sound to play when activation fails (Sound)
4603	 	ClearNoActivationSound	 
 	Clear the activation failure sound
4604	ref.	GetLastHitCritical	 
4605	 	AddMusic	UNK:57:Music
4606	 	UnusedFunction3	 
4607	ref.	UnusedFunction4	 
4608	 	SetPCToddler	Integer
4609	ref.	IsCombatTarget	Actor
4610	 	TriggerScreenBlood (tsb)	Integer
 	Trigger screen blood
4611	ref.	GetVATSRightAreaFree	UNK:4:TESObjectREFR
4612	ref.	GetVATSLeftAreaFree	UNK:4:TESObjectREFR
4613	ref.	GetVATSBackAreaFree	UNK:4:TESObjectREFR
4614	ref.	GetVATSFrontAreaFree	UNK:4:TESObjectREFR
4615	ref.	GetIsLockBroken	 
4616	 	IsPS3	 
4617	 	IsWin32	 
4618	ref.	GetVATSRightTargetVisible	UNK:4:TESObjectREFR
4619	ref.	GetVATSLeftTargetVisible	UNK:4:TESObjectREFR
4620	ref.	GetVATSBackTargetVisible	UNK:4:TESObjectREFR
4621	ref.	GetVATSFrontTargetVisible	UNK:4:TESObjectREFR
4622	ref.	AttachAshPile	Integer
4623	ref.	SetCriticalStage	UNK:58:CriticalStage
4624	ref.	IsInCriticalStage	UNK:58:CriticalStage
4625	ref.	RemoveFromAllFactions	 
4626	 	GetXPForNextLevel	 
4627	 	ShowLockpickMenuDebug (slpmd)	Integer
4628	 	ForceSave	 
4629	 	GetInfamy	UNK:17:Faction
4630	 	GetInfamyViolent	UNK:17:Faction
4631	 	GetInfamyNonViolent	UNK:17:Faction
4632	ref.	UnusedFunction5	 
4633	 	Sin	Float, Integer:Int
4634	 	Cos	Float, Integer:Int
4635	 	Tan	Float, Integer:Int
4636	 	Sqrt	Float
4637	 	Log	Float, Float
4638	 	Abs	Float
4639	 	GetQuestCompleted (GetQC)	Quest
4640	ref.	UnusedFunction6	 
4641	 	PipBoyRadioOff	 
4642	 	AutoDisplayObjectives	Integer
4643	 	IsGoreDisabled	 
4644	 	FadeSFX (FSFX)	Float
4645	ref.	SetMinimalUse	Integer
4646	 	IsSceneActionComplete	UNK:67:Scene , Integer
4647	 	ShowQuestStages (SQS)	Quest
4648	ref.	GetSpellUsageNum	UNK:11:Magic Item
4649	 	ForceRadioStationUpdate (FRSU)	 
4650	 	GetActorsInHigh	 
4651	 	HasLoaded3D	 
4652	 	DisableAllMines	 
4653	 	SetLastExtDoorActivated	UNK:4:ObjectReferenceID
4654	 	KillQuestUpdates (KQU)	 
4655	 	IsImageSpaceActive	UNK:42:Imagespace Modifier ID
4656	ref.	HasKeyword	UNK:59:Keyword
4657	ref.	HasRefType	UNK:60:RefType
4658	ref.	LocationHasKeyword	UNK:59:Keyword
4659	ref.	LocationHasRefType	UNK:60:RefType
4660	 	CreateEvent	UNK:59:Keyword, UNK:61:Location, UNK:4:Ref 1, UNK:4:Ref 2, Integer:Value 1, Integer:Value 2
4661	ref.	GetIsEditorLocation	UNK:61:Location
4662	ref.	GetIsAliasRef	UNK:63:QuestAlias
4663	ref.	GetIsEditorLocAlias	UNK:63:QuestAlias
4664	ref.	IsSprinting	 
4665	ref.	IsBlocking	 
4666	ref.	HasEquippedSpell (hasspell)	UNK:68:Casting Source
4667	ref.	GetCurrentCastingType (getcasting)	UNK:68:Casting Source
4668	ref.	GetCurrentDeliveryType (getdelivery)	UNK:68:Casting Source
4669	ref.	EquipSpell	UNK:7:Spell Item, UNK:68:Casting Source
4670	ref.	GetAttackState	 
 	0 = None, 1 = Draw, 2 = Swing, 3 = Hit, 4 = Next Attack, 5 = Follow Through, 6 = Bash
4671	 	GetAliasedRef	UNK:63:QuestAlias
4672	 	GetEventData	UNK:46:Event Function, UNK:47:Event Member, UNK:48:Data
4673	ref.	IsCloserToAThanB	UNK:4:TESObjectREFR, UNK:4:TESObjectREFR
4674	ref.	EquipShout	UNK:64:Shout
4675	ref.	GetEquippedShout	UNK:64:Shout
4676	ref.	IsBleedingOut	 
4677	 	UnlockWord	UNK:65:Word Of Power
4678	 	TeachWord	UNK:65:Word Of Power
4679	ref.	AddToContainer	UNK:4:TESObjectREFR, Integer:Flag
4680	ref.	GetRelativeAngle	UNK:4:ObjectReferenceID, UNK:8:Axis
4681	ref.	SendAnimEvent (sae)	String
 	Send an event to the animation graph.
4682	ref.	Shout	UNK:64:Shout, Integer, UNK:4:ObjectReferenceID
4683	ref.	AddShout	UNK:64:Shout
4684	ref.	RemoveShout	UNK:64:Shout
4685	ref.	GetMovementDirection	 
4686	ref.	IsInScene	 
4687	 	GetRefTypeDeadCount	UNK:61:Location, UNK:60:RefType
4688	 	GetRefTypeAliveCount	UNK:61:Location, UNK:60:RefType
4689	ref.	ApplyHavokImpulse	Float, Float, Float, Float
4690	ref.	GetIsFlying	 
4691	ref.	IsCurrentSpell	UNK:7:Spell Item, UNK:68:Casting Source
4692	ref.	SpellHasKeyword	UNK:68:Casting Source, UNK:59:Keyword
4693	ref.	GetEquippedItemType	UNK:68:Casting Source
4694	 	GetLocationAliasCleared	UNK:63:QuestAlias
4695	 	SetLocationAliasCleared	UNK:63:QuestAlias
4696	 	GetLocAliasRefTypeDeadCount	UNK:63:QuestAlias, UNK:60:RefType
4697	 	GetLocAliasRefTypeAliveCount	UNK:63:QuestAlias, UNK:60:RefType
4698	ref.	IsWardState	UNK:70:Ward State
4699	ref.	IsInSameCurrentLocAsRef	UNK:4:TESObjectREFR, UNK:59:Keyword
4700	ref.	IsInSameCurrentLocAsRefAlias	UNK:63:QuestAlias, UNK:59:Keyword
4701	 	LocAliasIsLocation	UNK:63:QuestAlias, UNK:61:Location
4702	 	GetKeywordDataForLocation	UNK:61:Location, UNK:59:Keyword
4703	 	SetKeywordDataForLocation	UNK:61:Location, UNK:59:Keyword, Float:Value
4704	 	GetKeywordDataForAlias	UNK:63:QuestAlias, UNK:59:Keyword
4705	 	SetKeywordDataForAlias	UNK:63:QuestAlias, UNK:59:Keyword, Float:Value
4706	 	LocAliasHasKeyword	UNK:63:QuestAlias, UNK:59:Keyword
4707	ref.	IsNullPackageData	UNK:71:PackageData (Possibly Null)
4708	ref.	GetNumericPackageData	UNK:72:PackageData (Numeric)
4709	ref.	IsFurnitureAnimType	UNK:73:Furniture Anim Type
4710	ref.	IsFurnitureEntryType	UNK:74:Furniture Entry Type
4711	ref.	GetHighestRelationshipRank	 
4712	ref.	GetLowestRelationshipRank	 
4713	ref.	HasAssociationTypeAny	UNK:69:AssociationType
4714	ref.	HasFamilyRelationshipAny	 
4715	ref.	GetPathingTargetOffset	UNK:8:Axis
4716	ref.	GetPathingTargetAngleOffset	UNK:8:Axis
4717	ref.	GetPathingTargetSpeed	 
4718	ref.	GetPathingTargetSpeedAngle	UNK:8:Axis
4719	ref.	GetMovementSpeed	 
4720	ref.	GetInContainer	UNK:4:TESObjectREFR
4721	 	IsLocationLoaded	UNK:61:Location
4722	 	IsLocAliasLoaded	UNK:63:QuestAlias
4723	ref.	IsDualCasting	 
4724	ref.	DualCast	UNK:11:Magic Item, UNK:4:ObjectReferenceID
4725	 	GetVMQuestVariable	Quest, UNK:76:VM Variable Name
4726	 	GetVMScriptVariable	UNK:4:ObjectReferenceID, UNK:76:VM Variable Name
4727	ref.	IsEnteringInteractionQuick	 
4728	ref.	IsCasting	 
4729	ref.	GetFlyingState	 
4730	ref.	SetFavorState	Integer
4731	ref.	IsInFavorState	 
4732	ref.	HasTwoHandedWeaponEquipped	 
4733	ref.	IsExitingInstant	 
4734	ref.	IsInFriendStatewithPlayer	 
4735	ref.	GetWithinDistance	UNK:4:ObjectReferenceID, Float
4736	ref.	GetActorValuePercent	UNK:5:Actor Value
4737	ref.	IsUnique	 
4738	ref.	GetLastBumpDirection	 
4739	 	CameraShake	Float, Float
4740	ref.	IsInFurnitureState	UNK:73:Furniture Anim Type
4741	ref.	GetIsInjured	 
4742	ref.	GetIsCrashLandRequest	 
4743	ref.	GetIsHastyLandRequest	 
4744	 	UpdateQuestInstanceGlobal	Quest, UNK:19:Global
4745	ref.	SetAllowFlying	Integer
4746	ref.	IsLinkedTo	UNK:4:TESObjectREFR, UNK:59:Keyword
4747	ref.	GetKeywordDataForCurrentLocation	UNK:59:Keyword
4748	ref.	GetInSharedCrimeFaction	UNK:4:TESObjectREFR
4749	ref.	GetBribeAmount	 
4750	ref.	GetBribeSuccess	 
4751	ref.	GetIntimidateSuccess	 
4752	ref.	GetArrestedState	 
4753	ref.	GetArrestingActor	 
4754	ref.	ClearArrestState	 
4755	 	EPTemperingItemIsEnchanted	 
4756	 	EPTemperingItemHasKeyword	UNK:59:Keyword
4757	ref.	GetReceivedGiftValue	 
4758	ref.	GetGiftGivenValue	 
4759	 	ForceLocIntoAlias	UNK:61:Location, UNK:63:QuestAlias
4760	ref.	GetReplacedItemType	UNK:68:Casting Source
4761	ref.	SetHorseActor	UNK:4:TESObjectREFR
4762	ref.	PlayReferenceEffect (pre)	UNK:77:Reference Effect, Float:Time, UNK:4:Aim-at target
4763	ref.	StopReferenceEffect (sre)	UNK:77:Reference Effect
4764	 	PlayShaderParticleGeometry (pspg)	UNK:62:Form, Float
4765	 	StopShaderParticleGeometry (sspg)	UNK:62:Form, Float
4766	 	ApplyImageSpaceModifierCrossFade (imodcf)	UNK:42:Imagespace Modifier ID, Float
 	Adds a crossfade imagespace modifier to the active list
4767	 	RemoveImageSpaceModifierCrossFade (rimodcf)	Float
 	Remove a crossfade imagespace modifier from the active list
4768	ref.	IsAttacking	 
4769	ref.	IsPowerAttacking	 
4770	ref.	IsLastHostileActor	 
4771	ref.	GetGraphVariableInt	String
4772	ref.	GetCurrentShoutVariation	 
4773	ref.	PlayImpactEffect (pie)	String, String, Float, Float, Float, Float, Integer:Int, Integer:Int
4774	ref.	ShouldAttackKill	Actor
4775	ref.	SendStealAlarm (steal)	Actor
4776	ref.	GetActivationHeight	 
4777	 	EPModSkillUsage_IsAdvanceSkill	UNK:5:Actor Value
4778	ref.	WornHasKeyword	UNK:59:Keyword
4779	ref.	GetPathingCurrentSpeed	 
4780	ref.	GetPathingCurrentSpeedAngle	UNK:8:Axis
4781	ref.	KnockAreaEffect (kae)	Float, Float
4782	ref.	InterruptCast	 
4783	 	AddFormToFormList	UNK:62:Form, UNK:62:Form
4784	 	RevertFormList	UNK:62:Form
4785	 	AddFormToLeveledList	UNK:62:Form, UNK:62:Form, Integer:Int, Integer:Int
4786	 	RevertLeveledList	UNK:62:Form
4787	 	EPModSkillUsage_AdvanceObjectHasKeyword	UNK:59:Keyword
4788	 	EPModSkillUsage_IsAdvanceAction	UNK:80:Skill Action
4789	 	EPMagic_SpellHasKeyword	UNK:59:Keyword
4790	ref.	GetNoBleedoutRecovery	 
4791	ref.	SetNoBleedoutRecovery	Integer
4792	 	EPMagic_SpellHasSkill	UNK:5:Actor Value
4793	 	IsAttackType	UNK:59:Keyword
4794	ref.	IsAllowedToFly	 
4795	ref.	HasMagicEffectKeyword	UNK:59:Keyword
4796	ref.	IsCommandedActor	 
4797	ref.	IsStaggered	 
4798	ref.	IsRecoiling	 
4799	ref.	IsExitingInteractionQuick	 
4800	ref.	IsPathing	 
4801	ref.	GetShouldHelp	Actor
4802	ref.	HasBoundWeaponEquipped	UNK:68:Casting Source
4803	ref.	GetCombatTargetHasKeyword (gcthk)	UNK:59:Keyword
4804	 	UpdateLevel	 
 	Update the player's level based on current skill usage amounts.
4805	ref.	GetCombatGroupMemberCount (gcgmc)	 
4806	ref.	IsIgnoringCombat	 
4807	ref.	GetLightLevel (gll)	 
4808	 	SavePCFace (spf)	String
4809	 	SpellHasCastingPerk	UNK:39:Perk
4810	ref.	IsBeingRidden	 
4811	ref.	IsUndead	 
4812	 	GetRealHoursPassed	 
4813	 	UnequipAll	 
4814	ref.	IsUnlockedDoor	 
4815	ref.	IsHostileToActor	Actor
4816	ref.	GetTargetHeight	UNK:4:TESObjectREFR
 	Get the delta on z between two references
4817	ref.	IsPoison	 
 	Is the object a Poison potion
4818	ref.	WornApparelHasKeywordCount	UNK:59:Keyword
4819	ref.	GetItemHealthPercent	 
4820	ref.	EffectWasDualCast	 
4821	ref.	GetKnockStateEnum	 
4822	 	DoesNotExist	 
