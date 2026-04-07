{
  TES4_Export_Records.pas
  =======================
  xEdit Pascal Script for TES IV (Oblivion) - Export Phase
  
  Exports all records from an Oblivion .esm/.esp to JSON-like text files
  that can be imported into Skyrim (TES V) using TES5_Import_Records.pas.

  Usage:
    1. Open the plugin in TES4Edit (xEdit for Oblivion)
    2. Select all records or the plugin in the tree
    3. Apply this script
    4. Exported data is written to <DataPath>\TES4Export\

  Record Type Mapping (TES4 -> TES5):
    ACTI -> ACTI    (Activator)
    ALCH -> ALCH    (Potion/Ingestible)
    AMMO -> AMMO    (Ammunition)
    ANIO -> ANIO    (Animated Object)
    APPA -> MISC    (Apparatus -> Misc Item, no TES5 equivalent)
    ARMO -> ARMO    (Armor, with biped slot remapping)
    BOOK -> BOOK    (Book)
    BSGN -> [SPEL]  (Birthsign -> Spells, no direct equivalent)
    CELL -> CELL    (Cell)
    CLAS -> CLAS    (Class)
    CLMT -> CLMT    (Climate)
    CLOT -> ARMO    (Clothing -> Armor with ArmorType=Clothing)
    CONT -> CONT    (Container)
    CREA -> NPC_    (Creature -> NPC, no CREA in TES5)
    CSTY -> CSTY    (Combat Style)
    DIAL -> DIAL    (Dialog Topic)
    DOOR -> DOOR    (Door)
    EFSH -> EFSH    (Effect Shader)
    ENCH -> ENCH    (Enchantment)
    EYES -> EYES    (Eyes)
    FACT -> FACT    (Faction)
    FLOR -> FLOR    (Flora)
    FURN -> FURN    (Furniture)
    GLOB -> GLOB    (Global)
    GMST -> GMST    (Game Setting)
    GRAS -> GRAS    (Grass)
    HAIR -> HDPT    (Hair -> Head Part)
    IDLE -> IDLE    (Idle Animation)
    INFO -> INFO    (Dialog Response)
    INGR -> INGR    (Ingredient)
    KEYM -> KEYM    (Key -> Misc/Key)
    LAND -> LAND    (Landscape)
    LIGH -> LIGH    (Light)
    LSCR -> LSCR    (Load Screen)
    LTEX -> LTEX    (Landscape Texture)
    LVLC -> LVLN    (Leveled Creature -> Leveled NPC)
    LVLI -> LVLI    (Leveled Item)
    LVSP -> LVSP    (Leveled Spell)
    MGEF -> MGEF    (Magic Effect)
    MISC -> MISC    (Misc Item)
    NPC_ -> NPC_    (NPC)
    PACK -> PACK    (Package)
    PGRD -> NAVM    (Path Grid -> Nav Mesh, partial)
    QUST -> QUST    (Quest)
    RACE -> RACE    (Race)
    REFR -> REFR    (Placed Object)
    ACHR -> ACHR    (Placed NPC)
    ACRE -> ACHR    (Placed Creature -> Placed NPC)
    REGN -> REGN    (Region)
    SBSP -> STAT    (Subspace -> Static, no TES5 equivalent)
    SCPT -> [note]  (Script -> needs Papyrus rewrite, export source)
    SGST -> SCRL    (Sigil Stone -> Scroll, closest equivalent)
    SKIL -> [none]  (Skill -> hardcoded in TES5)
    SLGM -> SLGM    (Soul Gem)
    SOUN -> SOUN/SNDR (Sound -> Sound Descriptor)
    SPEL -> SPEL    (Spell)
    STAT -> STAT    (Static)
    TREE -> TREE    (Tree)
    WATR -> WATR    (Water)
    WEAP -> WEAP    (Weapon)
    WRLD -> WRLD    (Worldspace)
    WTHR -> WTHR    (Weather)
    ROAD -> [skip]  (Road -> replaced by NAVM in TES5)
    PLYR -> [skip]  (Player Reference -> hardcoded)
}
unit TES4_Export_Records;

var
  slExport: TStringList;
  ExportPath: string;
  RecordCount: Integer;
  ErrorCount: Integer;
  SourceFileName: string;
  OutputFileName: string;
  // Parent tracking for child record hierarchy
  gCurrentWorldFormID: string;
  gCurrentCellFormID: string;
  gCurrentDialFormID: string;

//============================================================================
// Utility Functions
//============================================================================

function EscapeStr(s: string): string;
// Escape special characters for clean text output
begin
  Result := s;
  Result := StringReplace(Result, '\', '\\', [rfReplaceAll]);
  Result := StringReplace(Result, '"', '\"', [rfReplaceAll]);
  Result := StringReplace(Result, Chr(13) + Chr(10), '\n', [rfReplaceAll]);
  Result := StringReplace(Result, Chr(13), '\r', [rfReplaceAll]);
  Result := StringReplace(Result, Chr(10), '\n', [rfReplaceAll]);
  Result := StringReplace(Result, Chr(9), '\t', [rfReplaceAll]);
end;

procedure WritePair(const key, value: string);
begin
  slExport.Add(key + '=' + value);
end;

procedure WriteStr(const key, value: string);
begin
  if value <> '' then
    slExport.Add(key + '=' + EscapeStr(value));
end;

procedure WriteInt(const key: string; value: Integer);
begin
  slExport.Add(key + '=' + IntToStr(value));
end;

procedure WriteFloat(const key: string; value: Double);
begin
  slExport.Add(key + '=' + FloatToStr(value));
end;

function InsertTES4Prefix(const path: string): string;
// Prepends 'tes4\' to the start of a file path
// e.g. meshes\folder\file.nif -> tes4\meshes\folder\file.nif
begin
  if path = '' then
    Result := ''
  else
    Result := 'tes4\' + path;
end;

procedure WritePath(const key, value: string);
// Like WriteStr but inserts tes4\ prefix into file paths
begin
  if value <> '' then
    slExport.Add(key + '=' + EscapeStr(InsertTES4Prefix(value)));
end;

procedure WriteFormID(const key: string; rec: IInterface);
begin
  if Assigned(rec) then
    slExport.Add(key + '=' + IntToHex(GetLoadOrderFormID(rec), 8))
  else
    slExport.Add(key + '=00000000');
end;

function SafeLinksTo(e: IInterface): IInterface;
// LinksTo crashes on nil elements or null (00000000) FormID fields
begin
  Result := nil;
  if not Assigned(e) then Exit;
  try
    Result := LinksTo(e);
  except
    Result := nil;
  end;
end;

procedure WriteRecordHeader(rec: IInterface);
begin
  slExport.Add('---RECORD_BEGIN---');
  WritePair('Signature', Signature(rec));
  WritePair('FormID', IntToHex(GetLoadOrderFormID(rec), 8));
  WriteStr('EditorID', EditorID(rec));
  WriteInt('RecordFlags', GetRecordFlag(rec));
end;

procedure WriteRecordEnd;
begin
  slExport.Add('---RECORD_END---');
  slExport.Add('');
end;

function SafeElementStr(rec: IInterface; const path: string): string;
begin
  Result := '';
  if ElementExists(rec, path) then
    Result := GetElementEditValues(rec, path);
end;

function SafeElementInt(rec: IInterface; const path: string): Integer;
begin
  Result := 0;
  if ElementExists(rec, path) then
    Result := GetElementNativeValues(rec, path);
end;

function SafeElementFloat(rec: IInterface; const path: string): Double;
begin
  Result := 0.0;
  if ElementExists(rec, path) then
    Result := GetElementNativeValues(rec, path);
end;

function GetRecordFlag(rec: IInterface): Integer;
begin
  Result := GetElementNativeValues(rec, 'Record Header\Record Flags');
end;

//============================================================================
// Common Sub-Record Exporters
//============================================================================

procedure ExportModel(rec: IInterface; const prefix: string);
begin
  WritePath(prefix + '.MODL', SafeElementStr(rec, 'Model\MODL'));
  // MODB (bound radius) and MODT (texture hash) are binary, skip
end;

procedure ExportIcon(rec: IInterface);
begin
  WritePath('ICON', SafeElementStr(rec, 'ICON'));
end;

procedure ExportFull(rec: IInterface);
begin
  WriteStr('FULL', SafeElementStr(rec, 'FULL'));
end;

procedure ExportDescription(rec: IInterface);
begin
  WriteStr('DESC', SafeElementStr(rec, 'DESC'));
end;

procedure ExportScript(rec: IInterface);
var
  scriptRef: IInterface;
begin
  scriptRef := ElementBySignature(rec, 'SCRI');
  if Assigned(scriptRef) then
    WriteFormID('SCRI', SafeLinksTo(scriptRef));
end;

procedure ExportEnchantment(rec: IInterface);
var
  enam: IInterface;
begin
  enam := ElementBySignature(rec, 'ENAM');
  if Assigned(enam) then begin
    WriteFormID('ENAM', SafeLinksTo(enam));
    WriteInt('ENAM.Charge', SafeElementInt(rec, 'ENAM\Charge Amount'));
  end;
end;

procedure ExportItems(rec: IInterface);
var
  items, item: IInterface;
  i: Integer;
begin
  items := ElementByName(rec, 'Items');
  if not Assigned(items) then Exit;
  WriteInt('ItemCount', ElementCount(items));
  for i := 0 to ElementCount(items) - 1 do begin
    item := ElementByIndex(items, i);
    WriteFormID('Item[' + IntToStr(i) + '].FormID', SafeLinksTo(ElementByPath(item, 'CNTO\Item')));
    WriteInt('Item[' + IntToStr(i) + '].Count', GetElementNativeValues(item, 'CNTO\Count'));
  end;
end;

procedure ExportSpells(rec: IInterface);
var
  spells: IInterface;
  i: Integer;
begin
  spells := ElementByName(rec, 'Spells');
  if not Assigned(spells) then Exit;
  WriteInt('SpellCount', ElementCount(spells));
  for i := 0 to ElementCount(spells) - 1 do
    WriteFormID('Spell[' + IntToStr(i) + ']', SafeLinksTo(ElementByIndex(spells, i)));
end;

procedure ExportConditions(rec: IInterface);
var
  conditions, cond: IInterface;
  i: Integer;
begin
  conditions := ElementByName(rec, 'Conditions');
  if not Assigned(conditions) then Exit;
  WriteInt('ConditionCount', ElementCount(conditions));
  for i := 0 to ElementCount(conditions) - 1 do begin
    cond := ElementByIndex(conditions, i);
    WriteStr('Cond[' + IntToStr(i) + '].Type', SafeElementStr(cond, 'CTDA\Type'));
    WriteStr('Cond[' + IntToStr(i) + '].CompValue', SafeElementStr(cond, 'CTDA\Comparison Value'));
    WriteStr('Cond[' + IntToStr(i) + '].Function', SafeElementStr(cond, 'CTDA\Function'));
    WriteStr('Cond[' + IntToStr(i) + '].Param1', SafeElementStr(cond, 'CTDA\Parameter #1'));
    WriteStr('Cond[' + IntToStr(i) + '].Param2', SafeElementStr(cond, 'CTDA\Parameter #2'));
  end;
end;

procedure ExportEffects(rec: IInterface);
var
  effects, effect, efit: IInterface;
  i: Integer;
begin
  effects := ElementByName(rec, 'Effects');
  if not Assigned(effects) then Exit;
  WriteInt('EffectCount', ElementCount(effects));
  for i := 0 to ElementCount(effects) - 1 do begin
    effect := ElementByIndex(effects, i);
    WriteStr('Effect[' + IntToStr(i) + '].EFID', SafeElementStr(effect, 'EFID'));
    WriteStr('Effect[' + IntToStr(i) + '].Magnitude', SafeElementStr(effect, 'EFIT\Magnitude'));
    WriteStr('Effect[' + IntToStr(i) + '].Area', SafeElementStr(effect, 'EFIT\Area'));
    WriteStr('Effect[' + IntToStr(i) + '].Duration', SafeElementStr(effect, 'EFIT\Duration'));
    WriteStr('Effect[' + IntToStr(i) + '].Type', SafeElementStr(effect, 'EFIT\Type'));
    WriteStr('Effect[' + IntToStr(i) + '].ActorValue', SafeElementStr(effect, 'EFIT\Actor Value'));
  end;
end;

procedure ExportFactions(rec: IInterface);
var
  factions, faction: IInterface;
  i: Integer;
begin
  factions := ElementByName(rec, 'Factions');
  if not Assigned(factions) then Exit;
  WriteInt('FactionCount', ElementCount(factions));
  for i := 0 to ElementCount(factions) - 1 do begin
    faction := ElementByIndex(factions, i);
    WriteFormID('Faction[' + IntToStr(i) + '].FormID', SafeLinksTo(ElementByPath(faction, 'Faction')));
    WriteInt('Faction[' + IntToStr(i) + '].Rank', GetElementNativeValues(faction, 'Rank'));
  end;
end;

procedure ExportAIPackages(rec: IInterface);
var
  packages: IInterface;
  i: Integer;
begin
  packages := ElementByName(rec, 'AI Packages');
  if not Assigned(packages) then Exit;
  WriteInt('AIPackageCount', ElementCount(packages));
  for i := 0 to ElementCount(packages) - 1 do
    WriteFormID('AIPackage[' + IntToStr(i) + ']', SafeLinksTo(ElementByIndex(packages, i)));
end;

procedure ExportPosRot(rec: IInterface);
begin
  WriteFloat('PosX', SafeElementFloat(rec, 'DATA\Position\X'));
  WriteFloat('PosY', SafeElementFloat(rec, 'DATA\Position\Y'));
  WriteFloat('PosZ', SafeElementFloat(rec, 'DATA\Position\Z'));
  WriteFloat('RotX', SafeElementFloat(rec, 'DATA\Rotation\X'));
  WriteFloat('RotY', SafeElementFloat(rec, 'DATA\Rotation\Y'));
  WriteFloat('RotZ', SafeElementFloat(rec, 'DATA\Rotation\Z'));
end;

//============================================================================
// Actor Value Mapping: TES4 -> TES5
//============================================================================
// TES4 Actor Values (0-71) need mapping to TES5 Actor Values
// Many TES4 skills/attributes don't exist in TES5

function MapActorValue(tes4AV: Integer): Integer;
begin
  // TES4: 0-7 = Attributes (Str,Int,Wil,Agi,Spd,End,Per,Luck)
  // TES5: No attributes system
  // TES4: 8=Health, 9=Magicka, 10=Fatigue -> TES5: 24=Health, 25=Magicka, 26=Stamina
  // TES4: 12-32 = Skills -> TES5: 6-23 = Skills (different set)
  case tes4AV of
    8:  Result := 24; // Health
    9:  Result := 25; // Magicka
    10: Result := 26; // Stamina (was Fatigue)
    // Skills - map to closest equivalent
    15: Result := 9;  // Block -> Block
    18: Result := 10; // Heavy Armor -> Heavy Armor
    27: Result := 12; // Light Armor -> Light Armor
    31: Result := 15; // Sneak -> Sneak
    19: Result := 16; // Alchemy -> Alchemy
    32: Result := 17; // Speechcraft -> Speech
    20: Result := 18; // Alteration -> Alteration
    21: Result := 19; // Conjuration -> Conjuration
    22: Result := 20; // Destruction -> Destruction
    23: Result := 21; // Illusion -> Illusion
    25: Result := 22; // Restoration -> Restoration
    28: Result := 8;  // Marksman -> Archery
    14: Result := 7;  // Blade -> One-Handed
    16: Result := 7;  // Blunt -> One-Handed (approx)
    17: Result := 7;  // Hand to Hand -> One-Handed (approx)
    12: Result := 11; // Armorer -> Smithing
    30: Result := 13; // Security -> Lockpicking
    29: Result := 14; // Mercantile -> Pickpocket (approx)
    13: Result := 26; // Athletics -> Stamina (no equivalent skill)
    26: Result := 26; // Acrobatics -> Stamina (no equivalent skill)
    24: Result := 21; // Mysticism -> Illusion (closest)
  else
    Result := -1; // None / unmapped
  end;
end;

//============================================================================
// Biped Slot Mapping: TES4 -> TES5
//============================================================================
// TES4 biped flags (16-bit):
//   0=Head, 1=Hair, 2=Upper Body, 3=Lower Body, 4=Hand, 5=Foot,
//   6=Right Ring, 7=Left Ring, 8=Amulet, 9=Weapon, 10=Back Weapon,
//   11=Side Weapon, 12=Quiver, 13=Shield, 14=Torch, 15=Tail
// TES5 body template uses BOD2 with 32-bit First Person Flags

function MapBipedSlotToTES5(tes4Flags: Integer): Integer;
begin
  Result := 0;
  // TES4 Head (0) -> TES5 30 - Head (bit 0)
  if (tes4Flags and 1) <> 0 then Result := Result or 1;
  // TES4 Hair (1) -> TES5 31 - Hair (bit 1)
  if (tes4Flags and 2) <> 0 then Result := Result or 2;
  // TES4 Upper Body (2) -> TES5 32 - Body (bit 2)
  if (tes4Flags and 4) <> 0 then Result := Result or 4;
  // TES4 Lower Body (3) -> TES5 32 - Body (shared with upper)
  if (tes4Flags and 8) <> 0 then Result := Result or 4;
  // TES4 Hand (4) -> TES5 33 - Hands (bit 3)
  if (tes4Flags and 16) <> 0 then Result := Result or 8;
  // TES4 Foot (5) -> TES5 37 - Feet (bit 7)
  if (tes4Flags and 32) <> 0 then Result := Result or 128;
  // TES4 Right Ring (6) -> TES5 36 - Ring (bit 6)
  if (tes4Flags and 64) <> 0 then Result := Result or 64;
  // TES4 Left Ring (7) -> TES5 36 - Ring (shared)
  if (tes4Flags and 128) <> 0 then Result := Result or 64;
  // TES4 Amulet (8) -> TES5 35 - Amulet (bit 5)
  if (tes4Flags and 256) <> 0 then Result := Result or 32;
  // TES4 Shield (13) -> TES5 39 - Shield (bit 9)
  if (tes4Flags and 8192) <> 0 then Result := Result or 512;
  // TES4 Tail (15) -> TES5 43 - Tail (bit 13)
  if (tes4Flags and 32768) <> 0 then Result := Result or 8192;
end;

//============================================================================
// Weapon Type Mapping: TES4 -> TES5
//============================================================================
// TES4: 0=Blade1H, 1=Blade2H, 2=Blunt1H, 3=Blunt2H, 4=Staff, 5=Bow
// TES5 Animation Type: 0=HandToHand, 1=Sword, 2=Dagger, 3=Waraxe,
//   4=Mace, 5=Greatsword, 6=Battleaxe/Warhammer, 7=Bow, 8=Staff,
//   9=Crossbow

function MapWeaponType(tes4Type: Integer): Integer;
begin
  case tes4Type of
    0: Result := 1; // Blade One Hand -> Sword
    1: Result := 5; // Blade Two Hand -> Greatsword
    2: Result := 4; // Blunt One Hand -> Mace
    3: Result := 6; // Blunt Two Hand -> Battleaxe/Warhammer
    4: Result := 8; // Staff -> Staff
    5: Result := 7; // Bow -> Bow
  else
    Result := 1;
  end;
end;

//============================================================================
// Magic Effect Code Mapping
//============================================================================
// TES4 uses 4-char codes (e.g. 'FIDG'), TES5 uses FormIDs for MGEF
// This exports the code so the importer can attempt to find the TES5 equivalent

//============================================================================
// Record Type Export Functions
//============================================================================

procedure ExportACTI(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'ACTI');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportScript(rec);
  WriteStr('Sound', SafeElementStr(rec, 'SNAM'));
  WriteRecordEnd;
end;

procedure ExportALCH(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'ALCH');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportIcon(rec);
  ExportScript(rec);
  WriteFloat('Weight', SafeElementFloat(rec, 'DATA'));
  WriteInt('ENIT.Value', SafeElementInt(rec, 'ENIT\Value'));
  WriteInt('ENIT.Flags', SafeElementInt(rec, 'ENIT\Flags'));
  ExportEffects(rec);
  WriteRecordEnd;
end;

procedure ExportAMMO(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'AMMO');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportIcon(rec);
  ExportEnchantment(rec);
  WriteFloat('DATA.Speed', SafeElementFloat(rec, 'DATA\Speed'));
  WriteInt('DATA.Value', SafeElementInt(rec, 'DATA\Value'));
  WriteFloat('DATA.Weight', SafeElementFloat(rec, 'DATA\Weight'));
  WriteInt('DATA.Damage', SafeElementInt(rec, 'DATA\Damage'));
  WriteInt('DATA.IgnoresResist', SafeElementInt(rec, 'DATA\Ignores Normal Weapon Resistance'));
  WriteRecordEnd;
end;

procedure ExportANIO(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'ANIO');
  ExportModel(rec, 'Model');
  WriteFormID('DATA.IdleAnim', SafeLinksTo(ElementBySignature(rec, 'DATA')));
  WriteRecordEnd;
end;

procedure ExportAPPA(rec: IInterface);
// Apparatus has no TES5 equivalent, convert to MISC
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'MISC');
  WritePair('OriginalType', 'APPA');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportIcon(rec);
  ExportScript(rec);
  WriteInt('DATA.Type', SafeElementInt(rec, 'DATA\Type'));
  WriteInt('DATA.Value', SafeElementInt(rec, 'DATA\Value'));
  WriteFloat('DATA.Weight', SafeElementFloat(rec, 'DATA\Weight'));
  WriteFloat('DATA.Quality', SafeElementFloat(rec, 'DATA\Quality'));
  WriteRecordEnd;
end;

procedure ExportARMO(rec: IInterface);
var
  bipedFlags, genFlags: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'ARMO');
  ExportFull(rec);
  ExportScript(rec);
  ExportEnchantment(rec);
  
  // Biped data
  bipedFlags := SafeElementInt(rec, 'BMDT\Biped Flags');
  genFlags := SafeElementInt(rec, 'BMDT\General Flags');
  WriteInt('BMDT.BipedFlags', bipedFlags);
  WriteInt('BMDT.GeneralFlags', genFlags);
  WriteInt('TES5.BipedSlots', MapBipedSlotToTES5(bipedFlags));
  
  // Is it heavy armor?
  if (genFlags and 128) <> 0 then
    WriteInt('ArmorType', 0) // Heavy
  else
    WriteInt('ArmorType', 1); // Light
  
  // Male models
  WritePath('Male.BipedModel', SafeElementStr(rec, 'Male\Biped Model\MODL'));
  WritePath('Male.WorldModel', SafeElementStr(rec, 'Male\World Model\MOD2'));
  WritePath('Male.Icon', SafeElementStr(rec, 'Male\ICON'));
  
  // Female models
  WritePath('Female.BipedModel', SafeElementStr(rec, 'Female\Biped Model\MOD3'));
  WritePath('Female.WorldModel', SafeElementStr(rec, 'Female\World Model\MOD4'));
  WritePath('Female.Icon', SafeElementStr(rec, 'Female\ICO2'));
  
  // DATA
  WriteInt('DATA.Armor', SafeElementInt(rec, 'DATA\Armor'));
  WriteInt('DATA.Value', SafeElementInt(rec, 'DATA\Value'));
  WriteInt('DATA.Health', SafeElementInt(rec, 'DATA\Health'));
  WriteFloat('DATA.Weight', SafeElementFloat(rec, 'DATA\Weight'));
  
  WriteRecordEnd;
end;

procedure ExportBOOK(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'BOOK');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportIcon(rec);
  ExportScript(rec);
  ExportEnchantment(rec);
  ExportDescription(rec);
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA\Flags'));
  WriteInt('DATA.Teaches', SafeElementInt(rec, 'DATA\Teaches'));
  WriteInt('DATA.Value', SafeElementInt(rec, 'DATA\Value'));
  WriteFloat('DATA.Weight', SafeElementFloat(rec, 'DATA\Weight'));
  WriteRecordEnd;
end;

procedure ExportBSGN(rec: IInterface);
// Birthsign -> exports to SPEL records (spells granted)
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'BSGN_SPELLS');
  ExportFull(rec);
  WritePath('ICON', SafeElementStr(rec, 'ICON'));
  ExportDescription(rec);
  ExportSpells(rec);
  WriteRecordEnd;
end;

procedure ExportCELL(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'CELL');
  ExportFull(rec);
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA'));
  
  // Parent worldspace for exterior cells  
  if gCurrentWorldFormID <> '' then
    WritePair('ParentWRLD', gCurrentWorldFormID);
  
  // Lighting
  if ElementExists(rec, 'XCLL') then begin
    WriteStr('XCLL.Ambient', SafeElementStr(rec, 'XCLL\Ambient Color'));
    WriteStr('XCLL.Directional', SafeElementStr(rec, 'XCLL\Directional Color'));
    WriteStr('XCLL.Fog', SafeElementStr(rec, 'XCLL\Fog Color'));
    WriteFloat('XCLL.FogNear', SafeElementFloat(rec, 'XCLL\Fog Near'));
    WriteFloat('XCLL.FogFar', SafeElementFloat(rec, 'XCLL\Fog Far'));
    WriteInt('XCLL.DirRotXY', SafeElementInt(rec, 'XCLL\Directional Rotation XY'));
    WriteInt('XCLL.DirRotZ', SafeElementInt(rec, 'XCLL\Directional Rotation Z'));
    WriteFloat('XCLL.DirFade', SafeElementFloat(rec, 'XCLL\Directional Fade'));
    WriteFloat('XCLL.FogClip', SafeElementFloat(rec, 'XCLL\Fog Clip Dist'));
  end;

  // Grid
  if ElementExists(rec, 'XCLC') then begin
    WriteInt('XCLC.X', SafeElementInt(rec, 'XCLC\X'));
    WriteInt('XCLC.Y', SafeElementInt(rec, 'XCLC\Y'));
  end;

  // Water
  if ElementExists(rec, 'XCLW') then
    WriteFloat('XCLW', SafeElementFloat(rec, 'XCLW'));
    
  WriteStr('Climate', SafeElementStr(rec, 'XCCM'));
  WriteStr('Water', SafeElementStr(rec, 'XCWT'));
  WriteStr('Music', SafeElementStr(rec, 'XCMT'));
  
  WriteRecordEnd;
end;

procedure ExportCLAS(rec: IInterface);
var
  majorSkills: IInterface;
  i: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'CLAS');
  ExportFull(rec);
  ExportDescription(rec);
  WriteStr('ICON', SafeElementStr(rec, 'ICON'));
  
  // DATA
  WriteInt('DATA.Attr1', SafeElementInt(rec, 'DATA\Primary Attributes\Attribute #1'));
  WriteInt('DATA.Attr2', SafeElementInt(rec, 'DATA\Primary Attributes\Attribute #2'));
  WriteInt('DATA.Specialization', SafeElementInt(rec, 'DATA\Specialization'));
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA\Flags'));
  WriteInt('DATA.Services', SafeElementInt(rec, 'DATA\Buys/Sells and Services'));
  WriteInt('DATA.Teaches', SafeElementInt(rec, 'DATA\Teaches'));
  WriteInt('DATA.MaxTraining', SafeElementInt(rec, 'DATA\Maximum training level'));
  
  // Major Skills (7 skills) - needed for TES5 skill weight conversion
  majorSkills := ElementByPath(rec, 'DATA\Major Skills');
  if Assigned(majorSkills) then begin
    for i := 0 to 6 do
      WriteInt('DATA.MajorSkill[' + IntToStr(i) + ']', GetElementNativeValues(majorSkills, 'Major Skill #' + IntToStr(i + 1)));
  end;
  
  WriteRecordEnd;
end;

procedure ExportCLMT(rec: IInterface);
var
  weathers, w: IInterface;
  i: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'CLMT');
  
  weathers := ElementBySignature(rec, 'WLST');
  if Assigned(weathers) then begin
    WriteInt('WeatherCount', ElementCount(weathers));
    for i := 0 to ElementCount(weathers) - 1 do begin
      w := ElementByIndex(weathers, i);
      WriteFormID('Weather[' + IntToStr(i) + '].FormID', SafeLinksTo(ElementByPath(w, 'Weather')));
      WriteInt('Weather[' + IntToStr(i) + '].Chance', GetElementNativeValues(w, 'Chance'));
    end;
  end;
  
  WritePath('SunTexture', SafeElementStr(rec, 'FNAM'));
  WritePath('SunGlare', SafeElementStr(rec, 'GNAM'));
  ExportModel(rec, 'Model');
  
  WriteRecordEnd;
end;

procedure ExportCLOT(rec: IInterface);
// Clothing -> ARMO in TES5 with ArmorType=Clothing
var
  bipedFlags: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'ARMO');
  WritePair('OriginalType', 'CLOT');
  ExportFull(rec);
  ExportScript(rec);
  ExportEnchantment(rec);

  bipedFlags := SafeElementInt(rec, 'BMDT\Biped Flags');
  WriteInt('BMDT.BipedFlags', bipedFlags);
  WriteInt('BMDT.GeneralFlags', SafeElementInt(rec, 'BMDT\General Flags'));
  WriteInt('TES5.BipedSlots', MapBipedSlotToTES5(bipedFlags));
  WriteInt('ArmorType', 2); // Clothing (TES5 enum)

  WritePath('Male.BipedModel', SafeElementStr(rec, 'Male\Biped Model\MODL'));
  WritePath('Male.WorldModel', SafeElementStr(rec, 'Male\World Model\MOD2'));
  WritePath('Male.Icon', SafeElementStr(rec, 'Male\ICON'));
  WritePath('Female.BipedModel', SafeElementStr(rec, 'Female\Biped Model\MOD3'));
  WritePath('Female.WorldModel', SafeElementStr(rec, 'Female\World Model\MOD4'));
  WritePath('Female.Icon', SafeElementStr(rec, 'Female\ICO2'));

  WriteInt('DATA.Value', SafeElementInt(rec, 'DATA\Value'));
  WriteFloat('DATA.Weight', SafeElementFloat(rec, 'DATA\Weight'));

  WriteRecordEnd;
end;

procedure ExportCONT(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'CONT');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportScript(rec);
  ExportItems(rec);
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA\Flags'));
  WriteFloat('DATA.Weight', SafeElementFloat(rec, 'DATA\Weight'));
  WriteStr('OpenSound', SafeElementStr(rec, 'SNAM'));
  WriteStr('CloseSound', SafeElementStr(rec, 'QNAM'));
  WriteRecordEnd;
end;

procedure ExportCREA(rec: IInterface);
// Creature -> NPC_ in TES5 (no CREA record type)
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'NPC_');
  WritePair('OriginalType', 'CREA');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportItems(rec);
  ExportSpells(rec);
  
  // ACBS
  WriteInt('ACBS.Flags', SafeElementInt(rec, 'ACBS\Flags'));
  WriteInt('ACBS.SpellPoints', SafeElementInt(rec, 'ACBS\Base spell points'));
  WriteInt('ACBS.Fatigue', SafeElementInt(rec, 'ACBS\Fatigue'));
  WriteInt('ACBS.BarterGold', SafeElementInt(rec, 'ACBS\Barter gold'));
  WriteInt('ACBS.Level', SafeElementInt(rec, 'ACBS\Level (offset)'));
  WriteInt('ACBS.CalcMin', SafeElementInt(rec, 'ACBS\Calc min'));
  WriteInt('ACBS.CalcMax', SafeElementInt(rec, 'ACBS\Calc max'));
  
  ExportFactions(rec);
  WriteFormID('DeathItem', SafeLinksTo(ElementBySignature(rec, 'INAM')));
  ExportScript(rec);
  
  // AI Data
  WriteInt('AIDT.Aggression', SafeElementInt(rec, 'AIDT\Aggression'));
  WriteInt('AIDT.Confidence', SafeElementInt(rec, 'AIDT\Confidence'));
  WriteInt('AIDT.EnergyLevel', SafeElementInt(rec, 'AIDT\Energy Level'));
  WriteInt('AIDT.Responsibility', SafeElementInt(rec, 'AIDT\Responsibility'));
  WriteInt('AIDT.Services', SafeElementInt(rec, 'AIDT\Buys/Sells and Services'));
  
  ExportAIPackages(rec);
  
  // Creature Data
  WriteInt('DATA.Type', SafeElementInt(rec, 'DATA\Type'));
  WriteInt('DATA.CombatSkill', SafeElementInt(rec, 'DATA\Combat Skill'));
  WriteInt('DATA.MagicSkill', SafeElementInt(rec, 'DATA\Magic Skill'));
  WriteInt('DATA.StealthSkill', SafeElementInt(rec, 'DATA\Stealth Skill'));
  WriteInt('DATA.Soul', SafeElementInt(rec, 'DATA\Soul'));
  WriteInt('DATA.Health', SafeElementInt(rec, 'DATA\Health'));
  WriteInt('DATA.AttackDamage', SafeElementInt(rec, 'DATA\Attack Damage'));
  WriteInt('DATA.Strength', SafeElementInt(rec, 'DATA\Strength'));
  WriteInt('DATA.Intelligence', SafeElementInt(rec, 'DATA\Intelligence'));
  WriteInt('DATA.Willpower', SafeElementInt(rec, 'DATA\Willpower'));
  WriteInt('DATA.Agility', SafeElementInt(rec, 'DATA\Agility'));
  WriteInt('DATA.Speed', SafeElementInt(rec, 'DATA\Speed'));
  WriteInt('DATA.Endurance', SafeElementInt(rec, 'DATA\Endurance'));
  WriteInt('DATA.Personality', SafeElementInt(rec, 'DATA\Personality'));
  WriteInt('DATA.Luck', SafeElementInt(rec, 'DATA\Luck'));
  
  WriteInt('AttackReach', SafeElementInt(rec, 'RNAM'));
  WriteFormID('CombatStyle', SafeLinksTo(ElementBySignature(rec, 'ZNAM')));
  WriteFloat('TurningSpeed', SafeElementFloat(rec, 'TNAM'));
  WriteFloat('BaseScale', SafeElementFloat(rec, 'BNAM'));
  WriteFloat('FootWeight', SafeElementFloat(rec, 'WNAM'));
  
  WriteRecordEnd;
end;

procedure ExportCSTY(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'CSTY');
  
  // Standard combat style data
  if ElementExists(rec, 'CSTD') then begin
    WriteInt('CSTD.DodgeChance', SafeElementInt(rec, 'CSTD\Dodge % Chance'));
    WriteInt('CSTD.BlockChance', SafeElementInt(rec, 'CSTD\Block % Chance'));
    WriteInt('CSTD.AttackChance', SafeElementInt(rec, 'CSTD\Attack % Chance'));
    WriteInt('CSTD.PowerAttackChance', SafeElementInt(rec, 'CSTD\Power Attack % Chance'));
    WriteFloat('CSTD.RangeMultOpt', SafeElementFloat(rec, 'CSTD\Range Mult (Optimal)'));
    WriteFloat('CSTD.RangeMultMax', SafeElementFloat(rec, 'CSTD\Range Mult (Max)'));
    WriteFloat('CSTD.SwitchMelee', SafeElementFloat(rec, 'CSTD\Switch Distance (Melee)'));
    WriteFloat('CSTD.SwitchRanged', SafeElementFloat(rec, 'CSTD\Switch Distance (Ranged)'));
    WriteFloat('CSTD.BuffStandoff', SafeElementFloat(rec, 'CSTD\Buff standoff Distance'));
    WriteFloat('CSTD.RangedStandoff', SafeElementFloat(rec, 'CSTD\Ranged standoff Distance'));
    WriteFloat('CSTD.GroupStandoff', SafeElementFloat(rec, 'CSTD\Group standoff Distance'));
    WriteInt('CSTD.Flags', SafeElementInt(rec, 'CSTD\Flags'));
  end;
  
  WriteRecordEnd;
end;

procedure ExportDIAL(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'DIAL');
  ExportFull(rec);
  WriteInt('DATA.Type', SafeElementInt(rec, 'DATA'));
  WriteRecordEnd;
end;

procedure ExportDOOR(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'DOOR');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportScript(rec);
  WriteStr('OpenSound', SafeElementStr(rec, 'SNAM'));
  WriteStr('CloseSound', SafeElementStr(rec, 'ANAM'));
  WriteStr('LoopSound', SafeElementStr(rec, 'BNAM'));
  WriteInt('FNAM.Flags', SafeElementInt(rec, 'FNAM'));
  WriteRecordEnd;
end;

procedure ExportEFSH(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'EFSH');
  WritePath('FillTexture', SafeElementStr(rec, 'ICON'));
  WritePath('ParticleTexture', SafeElementStr(rec, 'ICO2'));
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA\Flags'));
  
  // Membrane shader
  WriteInt('DATA.Membrane.SrcBlend', SafeElementInt(rec, 'DATA\Membrane Shader\Source Blend Mode'));
  WriteInt('DATA.Membrane.BlendOp', SafeElementInt(rec, 'DATA\Membrane Shader\Blend Operation'));
  WriteInt('DATA.Membrane.ZTest', SafeElementInt(rec, 'DATA\Membrane Shader\Z Test Function'));
  
  // Fill/texture colors
  WriteStr('DATA.FillColor', SafeElementStr(rec, 'DATA\Fill/Texture Effect\Color'));
  WriteFloat('DATA.FillAlphaFadeIn', SafeElementFloat(rec, 'DATA\Fill/Texture Effect\Alpha Fade In Time'));
  WriteFloat('DATA.FillFullAlpha', SafeElementFloat(rec, 'DATA\Fill/Texture Effect\Full Alpha Time'));
  WriteFloat('DATA.FillAlphaFadeOut', SafeElementFloat(rec, 'DATA\Fill/Texture Effect\Alpha Fade Out Time'));
  
  WriteRecordEnd;
end;

procedure ExportENCH(rec: IInterface);
var
  effects, effect: IInterface;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'ENCH');
  ExportFull(rec);
  WriteStr('ENIT.TypeName', SafeElementStr(rec, 'ENIT\Type'));
  WriteInt('ENIT.Type', SafeElementInt(rec, 'ENIT\Type'));
  WriteInt('ENIT.Charge', SafeElementInt(rec, 'ENIT\Charge Amount'));
  WriteInt('ENIT.Cost', SafeElementInt(rec, 'ENIT\Enchant Cost'));
  WriteInt('ENIT.AutoCalc', SafeElementInt(rec, 'ENIT\No Autocalc Cost'));
  // Export first effect type for TES5 Target Type derivation
  effects := ElementByName(rec, 'Effects');
  if Assigned(effects) then begin
    if ElementCount(effects) > 0 then begin
      effect := ElementByIndex(effects, 0);
      WriteInt('FirstEffect.Type', SafeElementInt(effect, 'EFIT\Type'));
    end;
  end;
  ExportEffects(rec);
  WriteRecordEnd;
end;

procedure ExportEYES(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'EYES');
  ExportFull(rec);
  WritePath('ICON', SafeElementStr(rec, 'ICON'));
  WriteInt('DATA.Playable', SafeElementInt(rec, 'DATA'));
  WriteRecordEnd;
end;

procedure ExportFACT(rec: IInterface);
var
  relations, rel, ranks, rank: IInterface;
  i: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'FACT');
  ExportFull(rec);
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA'));
  WriteFloat('CNAM.CrimeGoldMult', SafeElementFloat(rec, 'CNAM'));
  
  // Faction relations
  relations := ElementByName(rec, 'Relations');
  if Assigned(relations) then begin
    WriteInt('RelationCount', ElementCount(relations));
    for i := 0 to ElementCount(relations) - 1 do begin
      rel := ElementByIndex(relations, i);
      WriteFormID('Rel[' + IntToStr(i) + '].Faction', SafeLinksTo(ElementByPath(rel, 'Faction')));
      WriteInt('Rel[' + IntToStr(i) + '].Modifier', GetElementNativeValues(rel, 'Modifier'));
    end;
  end;
  
  // Ranks
  ranks := ElementByName(rec, 'Ranks');
  if Assigned(ranks) then begin
    WriteInt('RankCount', ElementCount(ranks));
    for i := 0 to ElementCount(ranks) - 1 do begin
      rank := ElementByIndex(ranks, i);
      WriteInt('Rank[' + IntToStr(i) + '].Number', GetElementNativeValues(rank, 'RNAM'));
      WriteStr('Rank[' + IntToStr(i) + '].Male', SafeElementStr(rank, 'MNAM'));
      WriteStr('Rank[' + IntToStr(i) + '].Female', SafeElementStr(rank, 'FNAM'));
    end;
  end;
  
  WriteRecordEnd;
end;

procedure ExportFLOR(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'FLOR');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportScript(rec);
  WriteFormID('Ingredient', SafeLinksTo(ElementBySignature(rec, 'PFIG')));
  WriteRecordEnd;
end;

procedure ExportFURN(rec: IInterface);
var
  mnam: IInterface;
  i: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'FURN');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportScript(rec);
  
  // MNAM - Marker Flags (bitmask for furniture animation markers)
  if ElementExists(rec, 'MNAM') then
    WriteInt('MNAM.MarkerFlags', SafeElementInt(rec, 'MNAM'));
  
  WriteRecordEnd;
end;

procedure ExportGLOB(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'GLOB');
  WriteStr('FNAM.Type', SafeElementStr(rec, 'FNAM'));
  WriteFloat('FLTV.Value', SafeElementFloat(rec, 'FLTV'));
  WriteRecordEnd;
end;

procedure ExportGMST(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'GMST');
  WriteStr('DATA.Value', SafeElementStr(rec, 'DATA'));
  WriteRecordEnd;
end;

procedure ExportGRAS(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'GRAS');
  ExportModel(rec, 'Model');
  WriteInt('DATA.Density', SafeElementInt(rec, 'DATA\Density'));
  WriteInt('DATA.MinSlope', SafeElementInt(rec, 'DATA\Min Slope'));
  WriteInt('DATA.MaxSlope', SafeElementInt(rec, 'DATA\Max Slope'));
  WriteInt('DATA.UnitFromWater', SafeElementInt(rec, 'DATA\Unit from water amount'));
  WriteInt('DATA.UnitFromWaterType', SafeElementInt(rec, 'DATA\Unit from water type'));
  WriteFloat('DATA.PosRange', SafeElementFloat(rec, 'DATA\Position Range'));
  WriteFloat('DATA.HeightRange', SafeElementFloat(rec, 'DATA\Height Range'));
  WriteFloat('DATA.ColorRange', SafeElementFloat(rec, 'DATA\Color Range'));
  WriteFloat('DATA.WavePeriod', SafeElementFloat(rec, 'DATA\Wave Period'));
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA\Flags'));
  WriteRecordEnd;
end;

procedure ExportHAIR(rec: IInterface);
// HAIR -> HDPT (Head Part) in TES5
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'HDPT');
  WritePair('OriginalType', 'HAIR');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  WritePath('ICON', SafeElementStr(rec, 'ICON'));
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA'));
  WriteRecordEnd;
end;

procedure ExportIDLE(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'IDLE');
  ExportModel(rec, 'Model');
  ExportConditions(rec);
  WriteInt('ANAM', SafeElementInt(rec, 'ANAM'));
  WriteFormID('DATA.Parent', SafeLinksTo(ElementByPath(rec, 'DATA\Parent')));
  WriteFormID('DATA.Previous', SafeLinksTo(ElementByPath(rec, 'DATA\Previous')));
  WriteRecordEnd;
end;

procedure ExportINFO(rec: IInterface);
var
  responses, resp, topics: IInterface;
  i: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'INFO');
  WritePair('ParentDIAL', gCurrentDialFormID);
  
  WriteInt('DATA.Type', SafeElementInt(rec, 'DATA\Type'));
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA\Flags'));
  WriteFormID('QSTI', SafeLinksTo(ElementBySignature(rec, 'QSTI')));
  WriteFormID('TPIC', SafeLinksTo(ElementBySignature(rec, 'TPIC')));
  WriteFormID('PNAM', SafeLinksTo(ElementBySignature(rec, 'PNAM')));
  
  // Add Topics
  topics := ElementByName(rec, 'Add Topics');
  if Assigned(topics) then begin
    WriteInt('AddTopicCount', ElementCount(topics));
    for i := 0 to ElementCount(topics) - 1 do
      WriteFormID('AddTopic[' + IntToStr(i) + ']', SafeLinksTo(ElementByIndex(topics, i)));
  end;
  
  // Responses
  responses := ElementByName(rec, 'Responses');
  if Assigned(responses) then begin
    WriteInt('ResponseCount', ElementCount(responses));
    for i := 0 to ElementCount(responses) - 1 do begin
      resp := ElementByIndex(responses, i);
      WriteInt('Resp[' + IntToStr(i) + '].EmotionType', SafeElementInt(resp, 'TRDT\Emotion Type'));
      WriteInt('Resp[' + IntToStr(i) + '].EmotionValue', SafeElementInt(resp, 'TRDT\Emotion Value'));
      WriteInt('Resp[' + IntToStr(i) + '].ResponseNum', SafeElementInt(resp, 'TRDT\Response Number'));
      WriteStr('Resp[' + IntToStr(i) + '].Text', SafeElementStr(resp, 'NAM1'));
      WriteStr('Resp[' + IntToStr(i) + '].Notes', SafeElementStr(resp, 'NAM2'));
    end;
  end;
  
  ExportConditions(rec);
  
  // Result Script (source only - needs Papyrus rewrite)
  WriteStr('ResultScript.Source', SafeElementStr(rec, 'Result Script\SCTX'));
  
  WriteRecordEnd;
end;

procedure ExportINGR(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'INGR');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportIcon(rec);
  ExportScript(rec);
  WriteFloat('DATA.Weight', SafeElementFloat(rec, 'DATA'));
  WriteInt('ENIT.Value', SafeElementInt(rec, 'ENIT\Value'));
  WriteInt('ENIT.Flags', SafeElementInt(rec, 'ENIT\Flags'));
  ExportEffects(rec);
  WriteRecordEnd;
end;

procedure ExportKEYM(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'KEYM');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportIcon(rec);
  ExportScript(rec);
  WriteInt('DATA.Value', SafeElementInt(rec, 'DATA\Value'));
  WriteFloat('DATA.Weight', SafeElementFloat(rec, 'DATA\Weight'));
  WriteRecordEnd;
end;

procedure ExportLAND(rec: IInterface);
var
  el, layers, layer, vtxtList, vtxt: IInterface;
  i, k, cnt: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'LAND');
  WritePair('ParentCELL', gCurrentCellFormID);
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA'));

  // NOTE: Vertex data (VHGT, VNML, VCLR) is intentionally NOT exported here.
  // LAND binary format is identical between TES4 and TES5, so vertex data
  // can be copied as raw binary in a post-processing step, which is orders of
  // magnitude faster than text-based export/import of 3000+ values per record.
  // Only structural/FormID data needed for placement and relinking is exported.

  // Layers - export structure only (for relink phase texture resolution)
  layers := ElementByName(rec, 'Layers');
  if Assigned(layers) then begin
    WriteInt('LayerCount', ElementCount(layers));
    for i := 0 to ElementCount(layers) - 1 do begin
      layer := ElementByIndex(layers, i);
      if ElementExists(layer, 'BTXT') then begin
        WritePair('Layer[' + IntToStr(i) + '].Type', 'BASE');
        WriteFormID('Layer[' + IntToStr(i) + '].BTXT.Texture',
          SafeLinksTo(ElementByPath(layer, 'BTXT\Texture')));
        WriteInt('Layer[' + IntToStr(i) + '].BTXT.Quadrant',
          SafeElementInt(layer, 'BTXT\Quadrant'));
      end
      else if ElementExists(layer, 'ATXT') then begin
        WritePair('Layer[' + IntToStr(i) + '].Type', 'ALPHA');
        WriteFormID('Layer[' + IntToStr(i) + '].ATXT.Texture',
          SafeLinksTo(ElementByPath(layer, 'ATXT\Texture')));
        WriteInt('Layer[' + IntToStr(i) + '].ATXT.Quadrant',
          SafeElementInt(layer, 'ATXT\Quadrant'));
        WriteInt('Layer[' + IntToStr(i) + '].ATXT.Layer',
          SafeElementInt(layer, 'ATXT\Layer'));
      end;
    end;
  end;

  // VTEX - texture FormID list (for relink)
  el := ElementBySignature(rec, 'VTEX');
  if Assigned(el) then begin
    WriteInt('VTEXCount', ElementCount(el));
    for i := 0 to ElementCount(el) - 1 do
      WriteFormID('VTEX[' + IntToStr(i) + ']', SafeLinksTo(ElementByIndex(el, i)));
  end;

  WriteRecordEnd;
end;

procedure ExportLIGH(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'LIGH');
  ExportModel(rec, 'Model');
  ExportScript(rec);
  ExportFull(rec);
  ExportIcon(rec);
  WriteInt('DATA.Time', SafeElementInt(rec, 'DATA\Time'));
  WriteInt('DATA.Radius', SafeElementInt(rec, 'DATA\Radius'));
  WriteStr('DATA.Color', SafeElementStr(rec, 'DATA\Color'));
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA\Flags'));
  WriteFloat('DATA.FalloffExp', SafeElementFloat(rec, 'DATA\Falloff Exponent'));
  WriteFloat('DATA.FOV', SafeElementFloat(rec, 'DATA\FOV'));
  WriteInt('DATA.Value', SafeElementInt(rec, 'DATA\Value'));
  WriteFloat('DATA.Weight', SafeElementFloat(rec, 'DATA\Weight'));
  WriteFloat('FNAM.Fade', SafeElementFloat(rec, 'FNAM'));
  WriteStr('Sound', SafeElementStr(rec, 'SNAM'));
  WriteRecordEnd;
end;

procedure ExportLSCR(rec: IInterface);
var
  locs, loc: IInterface;
  i: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'LSCR');
  ExportIcon(rec);
  ExportDescription(rec);
  
  locs := ElementByName(rec, 'Locations');
  if Assigned(locs) then begin
    WriteInt('LocationCount', ElementCount(locs));
    for i := 0 to ElementCount(locs) - 1 do begin
      loc := ElementByIndex(locs, i);
      WriteFormID('Loc[' + IntToStr(i) + '].Direct', SafeLinksTo(ElementByPath(loc, 'LNAM\Direct')));
      WriteFormID('Loc[' + IntToStr(i) + '].World', SafeLinksTo(ElementByPath(loc, 'LNAM\Indirect\World')));
      WriteInt('Loc[' + IntToStr(i) + '].GridY', SafeElementInt(loc, 'LNAM\Indirect\Grid Y'));
      WriteInt('Loc[' + IntToStr(i) + '].GridX', SafeElementInt(loc, 'LNAM\Indirect\Grid X'));
    end;
  end;
  
  WriteRecordEnd;
end;

procedure ExportLTEX(rec: IInterface);
var
  grasses: IInterface;
  i: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'LTEX');
  ExportIcon(rec);
  WriteInt('HNAM.Material', SafeElementInt(rec, 'HNAM\Material Type'));
  WriteInt('HNAM.Friction', SafeElementInt(rec, 'HNAM\Friction'));
  WriteInt('HNAM.Restitution', SafeElementInt(rec, 'HNAM\Restitution'));
  WriteInt('SNAM.Specular', SafeElementInt(rec, 'SNAM'));
  
  // Grasses
  if ElementExists(rec, 'Grasses') then begin
    grasses := ElementByName(rec, 'Grasses');
    WriteInt('GrassCount', ElementCount(grasses));
    for i := 0 to ElementCount(grasses) - 1 do
      WriteFormID('Grass[' + IntToStr(i) + ']', SafeLinksTo(ElementByIndex(grasses, i)));
  end;
  
  WriteRecordEnd;
end;

procedure ExportLVLC(rec: IInterface);
// Leveled Creature -> LVLN (Leveled NPC) in TES5
var
  entries, entry: IInterface;
  i: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'LVLN');
  WritePair('OriginalType', 'LVLC');
  WriteInt('LVLD.ChanceNone', SafeElementInt(rec, 'LVLD'));
  WriteInt('LVLF.Flags', SafeElementInt(rec, 'LVLF'));
  
  entries := ElementByName(rec, 'Leveled List Entries');
  if Assigned(entries) then begin
    WriteInt('EntryCount', ElementCount(entries));
    for i := 0 to ElementCount(entries) - 1 do begin
      entry := ElementByIndex(entries, i);
      WriteFormID('Entry[' + IntToStr(i) + '].FormID', SafeLinksTo(ElementByPath(entry, 'LVLO\Reference')));
      WriteInt('Entry[' + IntToStr(i) + '].Level', GetElementNativeValues(entry, 'LVLO\Level'));
      WriteInt('Entry[' + IntToStr(i) + '].Count', GetElementNativeValues(entry, 'LVLO\Count'));
    end;
  end;
  
  WriteFormID('Template', SafeLinksTo(ElementBySignature(rec, 'TNAM')));
  WriteRecordEnd;
end;

procedure ExportLVLI(rec: IInterface);
var
  entries, entry: IInterface;
  i: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'LVLI');
  WriteInt('LVLD.ChanceNone', SafeElementInt(rec, 'LVLD'));
  WriteInt('LVLF.Flags', SafeElementInt(rec, 'LVLF'));
  
  entries := ElementByName(rec, 'Leveled List Entries');
  if Assigned(entries) then begin
    WriteInt('EntryCount', ElementCount(entries));
    for i := 0 to ElementCount(entries) - 1 do begin
      entry := ElementByIndex(entries, i);
      WriteFormID('Entry[' + IntToStr(i) + '].FormID', SafeLinksTo(ElementByPath(entry, 'LVLO\Reference')));
      WriteInt('Entry[' + IntToStr(i) + '].Level', GetElementNativeValues(entry, 'LVLO\Level'));
      WriteInt('Entry[' + IntToStr(i) + '].Count', GetElementNativeValues(entry, 'LVLO\Count'));
    end;
  end;
  
  WriteRecordEnd;
end;

procedure ExportLVSP(rec: IInterface);
var
  entries, entry: IInterface;
  i: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'LVSP');
  WriteInt('LVLD.ChanceNone', SafeElementInt(rec, 'LVLD'));
  WriteInt('LVLF.Flags', SafeElementInt(rec, 'LVLF'));
  
  entries := ElementByName(rec, 'Leveled List Entries');
  if Assigned(entries) then begin
    WriteInt('EntryCount', ElementCount(entries));
    for i := 0 to ElementCount(entries) - 1 do begin
      entry := ElementByIndex(entries, i);
      WriteFormID('Entry[' + IntToStr(i) + '].FormID', SafeLinksTo(ElementByPath(entry, 'LVLO\Reference')));
      WriteInt('Entry[' + IntToStr(i) + '].Level', GetElementNativeValues(entry, 'LVLO\Level'));
      WriteInt('Entry[' + IntToStr(i) + '].Count', GetElementNativeValues(entry, 'LVLO\Count'));
    end;
  end;
  
  WriteRecordEnd;
end;

procedure ExportMGEF(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'MGEF');
  ExportFull(rec);
  ExportDescription(rec);
  ExportIcon(rec);
  ExportModel(rec, 'Model');
  
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA\Flags'));
  WriteFloat('DATA.BaseCost', SafeElementFloat(rec, 'DATA\Base cost'));
  WriteInt('DATA.MagicSchool', SafeElementInt(rec, 'DATA\Magic School'));
  WriteInt('DATA.ResistValue', SafeElementInt(rec, 'DATA\Resist value'));
  WriteInt('DATA.CounterEffectCount', SafeElementInt(rec, 'DATA\Counter Effect Count'));
  WriteFormID('DATA.Light', SafeLinksTo(ElementByPath(rec, 'DATA\Light')));
  WriteFloat('DATA.ProjSpeed', SafeElementFloat(rec, 'DATA\Projectile speed'));
  WriteFormID('DATA.EffectShader', SafeLinksTo(ElementByPath(rec, 'DATA\Effect Shader')));
  WriteFormID('DATA.EnchantEffect', SafeLinksTo(ElementByPath(rec, 'DATA\Enchant effect')));
  WriteFormID('DATA.CastingSound', SafeLinksTo(ElementByPath(rec, 'DATA\Casting sound')));
  WriteFormID('DATA.BoltSound', SafeLinksTo(ElementByPath(rec, 'DATA\Bolt sound')));
  WriteFormID('DATA.HitSound', SafeLinksTo(ElementByPath(rec, 'DATA\Hit sound')));
  WriteFormID('DATA.AreaSound', SafeLinksTo(ElementByPath(rec, 'DATA\Area sound')));
  WriteFloat('DATA.CEEnchFactor', SafeElementFloat(rec, 'DATA\Constant Effect enchantment factor'));
  WriteFloat('DATA.CEBarterFactor', SafeElementFloat(rec, 'DATA\Constant Effect barter factor'));
  
  WriteRecordEnd;
end;

procedure ExportMISC(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'MISC');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportIcon(rec);
  ExportScript(rec);
  WriteInt('DATA.Value', SafeElementInt(rec, 'DATA\Value'));
  WriteFloat('DATA.Weight', SafeElementFloat(rec, 'DATA\Weight'));
  WriteRecordEnd;
end;

procedure ExportNPC(rec: IInterface);
var
  eyes: IInterface;
  i: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'NPC_');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  
  // ACBS Configuration
  WriteInt('ACBS.Flags', SafeElementInt(rec, 'ACBS\Flags'));
  WriteInt('ACBS.SpellPoints', SafeElementInt(rec, 'ACBS\Base spell points'));
  WriteInt('ACBS.Fatigue', SafeElementInt(rec, 'ACBS\Fatigue'));
  WriteInt('ACBS.BarterGold', SafeElementInt(rec, 'ACBS\Barter gold'));
  WriteInt('ACBS.Level', SafeElementInt(rec, 'ACBS\Level (offset)'));
  WriteInt('ACBS.CalcMin', SafeElementInt(rec, 'ACBS\Calc min'));
  WriteInt('ACBS.CalcMax', SafeElementInt(rec, 'ACBS\Calc max'));
  
  ExportFactions(rec);
  WriteFormID('DeathItem', SafeLinksTo(ElementBySignature(rec, 'INAM')));
  WriteFormID('Race', SafeLinksTo(ElementBySignature(rec, 'RNAM')));
  ExportSpells(rec);
  ExportScript(rec);
  ExportItems(rec);
  
  // AI Data
  WriteInt('AIDT.Aggression', SafeElementInt(rec, 'AIDT\Aggression'));
  WriteInt('AIDT.Confidence', SafeElementInt(rec, 'AIDT\Confidence'));
  WriteInt('AIDT.EnergyLevel', SafeElementInt(rec, 'AIDT\Energy Level'));
  WriteInt('AIDT.Responsibility', SafeElementInt(rec, 'AIDT\Responsibility'));
  WriteInt('AIDT.Services', SafeElementInt(rec, 'AIDT\Buys/Sells and Services'));
  
  ExportAIPackages(rec);
  WriteFormID('Class', SafeLinksTo(ElementBySignature(rec, 'CNAM')));
  
  // Stats (skills + attributes)
  WriteInt('DATA.Armorer', SafeElementInt(rec, 'DATA\Armorer'));
  WriteInt('DATA.Athletics', SafeElementInt(rec, 'DATA\Athletics'));
  WriteInt('DATA.Blade', SafeElementInt(rec, 'DATA\Blade'));
  WriteInt('DATA.Block', SafeElementInt(rec, 'DATA\Block'));
  WriteInt('DATA.Blunt', SafeElementInt(rec, 'DATA\Blunt'));
  WriteInt('DATA.HandToHand', SafeElementInt(rec, 'DATA\Hand to Hand'));
  WriteInt('DATA.HeavyArmor', SafeElementInt(rec, 'DATA\Heavy Armor'));
  WriteInt('DATA.Alchemy', SafeElementInt(rec, 'DATA\Alchemy'));
  WriteInt('DATA.Alteration', SafeElementInt(rec, 'DATA\Alteration'));
  WriteInt('DATA.Conjuration', SafeElementInt(rec, 'DATA\Conjuration'));
  WriteInt('DATA.Destruction', SafeElementInt(rec, 'DATA\Destruction'));
  WriteInt('DATA.Illusion', SafeElementInt(rec, 'DATA\Illusion'));
  WriteInt('DATA.Mysticism', SafeElementInt(rec, 'DATA\Mysticism'));
  WriteInt('DATA.Restoration', SafeElementInt(rec, 'DATA\Restoration'));
  WriteInt('DATA.Acrobatics', SafeElementInt(rec, 'DATA\Acrobatics'));
  WriteInt('DATA.LightArmor', SafeElementInt(rec, 'DATA\Light Armor'));
  WriteInt('DATA.Marksman', SafeElementInt(rec, 'DATA\Marksman'));
  WriteInt('DATA.Mercantile', SafeElementInt(rec, 'DATA\Mercantile'));
  WriteInt('DATA.Security', SafeElementInt(rec, 'DATA\Security'));
  WriteInt('DATA.Sneak', SafeElementInt(rec, 'DATA\Sneak'));
  WriteInt('DATA.Speechcraft', SafeElementInt(rec, 'DATA\Speechcraft'));
  WriteInt('DATA.Health', SafeElementInt(rec, 'DATA\Health'));
  WriteInt('DATA.Strength', SafeElementInt(rec, 'DATA\Strength'));
  WriteInt('DATA.Intelligence', SafeElementInt(rec, 'DATA\Intelligence'));
  WriteInt('DATA.Willpower', SafeElementInt(rec, 'DATA\Willpower'));
  WriteInt('DATA.Agility', SafeElementInt(rec, 'DATA\Agility'));
  WriteInt('DATA.Speed', SafeElementInt(rec, 'DATA\Speed'));
  WriteInt('DATA.Endurance', SafeElementInt(rec, 'DATA\Endurance'));
  WriteInt('DATA.Personality', SafeElementInt(rec, 'DATA\Personality'));
  WriteInt('DATA.Luck', SafeElementInt(rec, 'DATA\Luck'));
  
  WriteFormID('Hair', SafeLinksTo(ElementBySignature(rec, 'HNAM')));
  WriteFloat('HairLength', SafeElementFloat(rec, 'LNAM'));
  WriteStr('HairColor', SafeElementStr(rec, 'HCLR'));
  WriteFormID('CombatStyle', SafeLinksTo(ElementBySignature(rec, 'ZNAM')));
  
  // Eyes
  if ElementExists(rec, 'ENAM') then begin
    eyes := ElementBySignature(rec, 'ENAM');
    if Assigned(eyes) then begin
      WriteInt('EyeCount', ElementCount(eyes));
      for i := 0 to ElementCount(eyes) - 1 do
        WriteFormID('Eye[' + IntToStr(i) + ']', SafeLinksTo(ElementByIndex(eyes, i)));
    end;
  end;
  
  WriteRecordEnd;
end;

procedure ExportPACK(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'PACK');
  WriteInt('PKDT.Flags', SafeElementInt(rec, 'PKDT\Flags'));
  WriteStr('PKDT.Type', SafeElementStr(rec, 'PKDT\Type'));
  
  // Location
  if ElementExists(rec, 'PLDT') then begin
    WriteInt('PLDT.Type', SafeElementInt(rec, 'PLDT\Type'));
    WriteStr('PLDT.Location', SafeElementStr(rec, 'PLDT\Location'));
    WriteInt('PLDT.Radius', SafeElementInt(rec, 'PLDT\Radius'));
  end;
  
  // Schedule
  if ElementExists(rec, 'PSDT') then begin
    WriteInt('PSDT.Month', SafeElementInt(rec, 'PSDT\Month'));
    WriteInt('PSDT.DayOfWeek', SafeElementInt(rec, 'PSDT\Day of week'));
    WriteInt('PSDT.Date', SafeElementInt(rec, 'PSDT\Date'));
    WriteInt('PSDT.Time', SafeElementInt(rec, 'PSDT\Time'));
    WriteInt('PSDT.Duration', SafeElementInt(rec, 'PSDT\Duration'));
  end;
  
  // Target
  if ElementExists(rec, 'PTDT') then begin
    WriteInt('PTDT.Type', SafeElementInt(rec, 'PTDT\Type'));
    WriteStr('PTDT.Target', SafeElementStr(rec, 'PTDT\Target'));
    WriteInt('PTDT.Count', SafeElementInt(rec, 'PTDT\Count'));
  end;
  
  ExportConditions(rec);
  WriteRecordEnd;
end;

procedure ExportQUST(rec: IInterface);
var
  stages, stage, logs, logEntry, targets, target: IInterface;
  i, j: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'QUST');
  ExportScript(rec);
  ExportFull(rec);
  ExportIcon(rec);
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA\Flags'));
  WriteInt('DATA.Priority', SafeElementInt(rec, 'DATA\Priority'));
  ExportConditions(rec);
  
  // Stages
  stages := ElementByName(rec, 'Stages');
  if Assigned(stages) then begin
    WriteInt('StageCount', ElementCount(stages));
    for i := 0 to ElementCount(stages) - 1 do begin
      stage := ElementByIndex(stages, i);
      WriteInt('Stage[' + IntToStr(i) + '].Index', GetElementNativeValues(stage, 'INDX'));
      
      logs := ElementByName(stage, 'Log Entries');
      if Assigned(logs) then begin
        WriteInt('Stage[' + IntToStr(i) + '].LogCount', ElementCount(logs));
        for j := 0 to ElementCount(logs) - 1 do begin
          logEntry := ElementByIndex(logs, j);
          WriteInt('Stage[' + IntToStr(i) + '].Log[' + IntToStr(j) + '].Complete', 
            SafeElementInt(logEntry, 'QSDT'));
          WriteStr('Stage[' + IntToStr(i) + '].Log[' + IntToStr(j) + '].Text',
            SafeElementStr(logEntry, 'CNAM'));
        end;
      end;
    end;
  end;
  
  // Targets
  targets := ElementByName(rec, 'Targets');
  if Assigned(targets) then begin
    WriteInt('TargetCount', ElementCount(targets));
    for i := 0 to ElementCount(targets) - 1 do begin
      target := ElementByIndex(targets, i);
      WriteFormID('Target[' + IntToStr(i) + '].Ref', SafeLinksTo(ElementByPath(target, 'QSTA\Target')));
    end;
  end;
  
  WriteRecordEnd;
end;

procedure ExportRACE(rec: IInterface);
var
  skillBoosts, sb, hairs, eyes: IInterface;
  i: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'RACE');
  ExportFull(rec);
  ExportDescription(rec);
  ExportSpells(rec);
  
  // Skill Boosts
  skillBoosts := ElementByPath(rec, 'DATA\Skill Boosts');
  if Assigned(skillBoosts) then begin
    WriteInt('SkillBoostCount', ElementCount(skillBoosts));
    for i := 0 to ElementCount(skillBoosts) - 1 do begin
      sb := ElementByIndex(skillBoosts, i);
      WriteInt('SkillBoost[' + IntToStr(i) + '].Skill', GetElementNativeValues(sb, 'Skill'));
      WriteInt('SkillBoost[' + IntToStr(i) + '].Boost', GetElementNativeValues(sb, 'Boost'));
    end;
  end;
  
  WriteFloat('DATA.MaleHeight', SafeElementFloat(rec, 'DATA\Male Height'));
  WriteFloat('DATA.FemaleHeight', SafeElementFloat(rec, 'DATA\Female Height'));
  WriteFloat('DATA.MaleWeight', SafeElementFloat(rec, 'DATA\Male Weight'));
  WriteFloat('DATA.FemaleWeight', SafeElementFloat(rec, 'DATA\Female Weight'));
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA\Flags'));
  WriteInt('DATA.Playable', SafeElementInt(rec, 'DATA\Playable'));
  
  // Voice types as FormIDs for relinking
  WriteFormID('Voice.Male', SafeLinksTo(ElementByPath(rec, 'VNAM\Male')));
  WriteFormID('Voice.Female', SafeLinksTo(ElementByPath(rec, 'VNAM\Female')));
  
  // Default hair
  WriteFormID('DefaultHair.Male', SafeLinksTo(ElementByPath(rec, 'DNAM\Default Hair Male')));
  WriteFormID('DefaultHair.Female', SafeLinksTo(ElementByPath(rec, 'DNAM\Default Hair Female')));
  WriteInt('DefaultHairColor', SafeElementInt(rec, 'CNAM'));
  
  // FaceGen clamps
  WriteFloat('FaceGen.MainClamp', SafeElementFloat(rec, 'PNAM'));
  WriteFloat('FaceGen.FaceClamp', SafeElementFloat(rec, 'UNAM'));
  
  // Base attributes
  WriteInt('ATTR.Male.Strength', SafeElementInt(rec, 'ATTR\Male\Strength'));
  WriteInt('ATTR.Male.Intelligence', SafeElementInt(rec, 'ATTR\Male\Intelligence'));
  WriteInt('ATTR.Male.Willpower', SafeElementInt(rec, 'ATTR\Male\Willpower'));
  WriteInt('ATTR.Male.Agility', SafeElementInt(rec, 'ATTR\Male\Agility'));
  WriteInt('ATTR.Male.Speed', SafeElementInt(rec, 'ATTR\Male\Speed'));
  WriteInt('ATTR.Male.Endurance', SafeElementInt(rec, 'ATTR\Male\Endurance'));
  WriteInt('ATTR.Male.Personality', SafeElementInt(rec, 'ATTR\Male\Personality'));
  WriteInt('ATTR.Male.Luck', SafeElementInt(rec, 'ATTR\Male\Luck'));
  WriteInt('ATTR.Female.Strength', SafeElementInt(rec, 'ATTR\Female\Strength'));
  WriteInt('ATTR.Female.Intelligence', SafeElementInt(rec, 'ATTR\Female\Intelligence'));
  WriteInt('ATTR.Female.Willpower', SafeElementInt(rec, 'ATTR\Female\Willpower'));
  WriteInt('ATTR.Female.Agility', SafeElementInt(rec, 'ATTR\Female\Agility'));
  WriteInt('ATTR.Female.Speed', SafeElementInt(rec, 'ATTR\Female\Speed'));
  WriteInt('ATTR.Female.Endurance', SafeElementInt(rec, 'ATTR\Female\Endurance'));
  WriteInt('ATTR.Female.Personality', SafeElementInt(rec, 'ATTR\Female\Personality'));
  WriteInt('ATTR.Female.Luck', SafeElementInt(rec, 'ATTR\Female\Luck'));
  
  // Hair list
  hairs := ElementBySignature(rec, 'HNAM');
  if Assigned(hairs) then begin
    WriteInt('HairCount', ElementCount(hairs));
    for i := 0 to ElementCount(hairs) - 1 do
      WriteFormID('Hair[' + IntToStr(i) + ']', SafeLinksTo(ElementByIndex(hairs, i)));
  end;
  
  // Eyes list
  eyes := ElementBySignature(rec, 'ENAM');
  if Assigned(eyes) then begin
    WriteInt('EyeCount', ElementCount(eyes));
    for i := 0 to ElementCount(eyes) - 1 do
      WriteFormID('Eye[' + IntToStr(i) + ']', SafeLinksTo(ElementByIndex(eyes, i)));
  end;
  
  // Faction relations
  ExportFactions(rec);
  
  WriteRecordEnd;
end;

procedure ExportREFR(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'REFR');
  WritePair('ParentCELL', gCurrentCellFormID);
  WriteFormID('NAME', SafeLinksTo(ElementBySignature(rec, 'NAME')));
  
  // Teleport
  if ElementExists(rec, 'XTEL') then begin
    WriteFormID('XTEL.Door', SafeLinksTo(ElementByPath(rec, 'XTEL\Door')));
    WriteFloat('XTEL.PosX', SafeElementFloat(rec, 'XTEL\Position/Rotation\Position\X'));
    WriteFloat('XTEL.PosY', SafeElementFloat(rec, 'XTEL\Position/Rotation\Position\Y'));
    WriteFloat('XTEL.PosZ', SafeElementFloat(rec, 'XTEL\Position/Rotation\Position\Z'));
    WriteFloat('XTEL.RotX', SafeElementFloat(rec, 'XTEL\Position/Rotation\Rotation\X'));
    WriteFloat('XTEL.RotY', SafeElementFloat(rec, 'XTEL\Position/Rotation\Rotation\Y'));
    WriteFloat('XTEL.RotZ', SafeElementFloat(rec, 'XTEL\Position/Rotation\Rotation\Z'));
  end;
  
  // Lock
  if ElementExists(rec, 'XLOC') then begin
    WriteInt('XLOC.Level', SafeElementInt(rec, 'XLOC\Lock Level'));
    WriteFormID('XLOC.Key', SafeLinksTo(ElementByPath(rec, 'XLOC\Key')));
    WriteInt('XLOC.Flags', SafeElementInt(rec, 'XLOC\Flags'));
  end;
  
  // Enable Parent
  if ElementExists(rec, 'XESP') then begin
    WriteFormID('XESP.Ref', SafeLinksTo(ElementByPath(rec, 'XESP\Reference')));
    WriteInt('XESP.Opposite', SafeElementInt(rec, 'XESP\Set Enable State To Opposite Of Parent'));
  end;
  
  // Scale
  if ElementExists(rec, 'XSCL') then
    WriteFloat('XSCL', SafeElementFloat(rec, 'XSCL'));
  
  // Count
  if ElementExists(rec, 'XCNT') then
    WriteInt('XCNT', SafeElementInt(rec, 'XCNT'));
  
  // Level modifier
  if ElementExists(rec, 'XLCM') then
    WriteInt('XLCM', SafeElementInt(rec, 'XLCM'));
  
  // Map Marker
  if ElementExists(rec, 'Map Marker') then begin
    WritePair('HasMapMarker', 'True');
    WriteInt('MapMarker.Flags', SafeElementInt(rec, 'Map Marker\FNAM'));
    WriteStr('MapMarker.Name', SafeElementStr(rec, 'Map Marker\FULL'));
    WriteInt('MapMarker.Type', SafeElementInt(rec, 'Map Marker\TNAM\Type'));
  end;
  
  ExportPosRot(rec);
  WriteRecordEnd;
end;

procedure ExportACHR(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'ACHR');
  WritePair('ParentCELL', gCurrentCellFormID);
  WriteFormID('NAME', SafeLinksTo(ElementBySignature(rec, 'NAME')));
  
  if ElementExists(rec, 'XESP') then begin
    WriteFormID('XESP.Ref', SafeLinksTo(ElementByPath(rec, 'XESP\Reference')));
    WriteInt('XESP.Opposite', SafeElementInt(rec, 'XESP\Set Enable State To Opposite Of Parent'));
  end;
  
  if ElementExists(rec, 'XSCL') then
    WriteFloat('XSCL', SafeElementFloat(rec, 'XSCL'));
  
  ExportPosRot(rec);
  WriteRecordEnd;
end;

procedure ExportACRE(rec: IInterface);
// ACRE -> ACHR in TES5 (placed creature -> placed NPC)
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'ACHR');
  WritePair('OriginalType', 'ACRE');
  WritePair('ParentCELL', gCurrentCellFormID);
  WriteFormID('NAME', SafeLinksTo(ElementBySignature(rec, 'NAME')));
  
  if ElementExists(rec, 'XESP') then begin
    WriteFormID('XESP.Ref', SafeLinksTo(ElementByPath(rec, 'XESP\Reference')));
    WriteInt('XESP.Opposite', SafeElementInt(rec, 'XESP\Set Enable State To Opposite Of Parent'));
  end;
  
  if ElementExists(rec, 'XSCL') then
    WriteFloat('XSCL', SafeElementFloat(rec, 'XSCL'));
  
  ExportPosRot(rec);
  WriteRecordEnd;
end;

procedure ExportREGN(rec: IInterface);
var
  entries, entry, objects, obj, weathers, w, sounds, snd: IInterface;
  i, j: Integer;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'REGN');
  ExportIcon(rec);
  WriteStr('RCLR', SafeElementStr(rec, 'RCLR'));
  WriteFormID('WNAM', SafeLinksTo(ElementBySignature(rec, 'WNAM')));
  
  entries := ElementByName(rec, 'Region Data Entries');
  if Assigned(entries) then begin
    WriteInt('DataEntryCount', ElementCount(entries));
    for i := 0 to ElementCount(entries) - 1 do begin
      entry := ElementByIndex(entries, i);
      WriteInt('Entry[' + IntToStr(i) + '].Type', SafeElementInt(entry, 'RDAT\Type'));
      WriteInt('Entry[' + IntToStr(i) + '].Override', SafeElementInt(entry, 'RDAT\Override'));
      WriteInt('Entry[' + IntToStr(i) + '].Priority', SafeElementInt(entry, 'RDAT\Priority'));
      WriteStr('Entry[' + IntToStr(i) + '].MapName', SafeElementStr(entry, 'RDMP'));
    end;
  end;
  
  WriteRecordEnd;
end;

procedure ExportSBSP(rec: IInterface);
// Subspace -> STAT in TES5
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'STAT');
  WritePair('OriginalType', 'SBSP');
  WriteFloat('DNAM.X', SafeElementFloat(rec, 'DNAM\X'));
  WriteFloat('DNAM.Y', SafeElementFloat(rec, 'DNAM\Y'));
  WriteFloat('DNAM.Z', SafeElementFloat(rec, 'DNAM\Z'));
  WriteRecordEnd;
end;

procedure ExportSCPT(rec: IInterface);
// Script -> needs Papyrus rewrite; export source for reference
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'SCPT_SOURCE');
  WritePair('Note', 'TES4 scripts must be rewritten in Papyrus for TES5');
  WriteStr('SCTX', SafeElementStr(rec, 'SCTX'));
  WriteRecordEnd;
end;

procedure ExportSGST(rec: IInterface);
// Sigil Stone -> SCRL (Scroll) in TES5 as closest equivalent
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'SCRL');
  WritePair('OriginalType', 'SGST');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportIcon(rec);
  ExportScript(rec);
  ExportEffects(rec);
  WriteInt('DATA.Uses', SafeElementInt(rec, 'DATA\Uses '));
  WriteInt('DATA.Value', SafeElementInt(rec, 'DATA\Value'));
  WriteFloat('DATA.Weight', SafeElementFloat(rec, 'DATA\Weight'));
  WriteRecordEnd;
end;

procedure ExportSKIL(rec: IInterface);
// Skill -> No TES5 equivalent, export for reference
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'SKIL_REF');
  WritePair('Note', 'Skills are hardcoded in TES5 - exported for reference only');
  WriteInt('INDX', SafeElementInt(rec, 'INDX'));
  ExportDescription(rec);
  WriteRecordEnd;
end;

procedure ExportSLGM(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'SLGM');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportIcon(rec);
  ExportScript(rec);
  WriteInt('DATA.Value', SafeElementInt(rec, 'DATA\Value'));
  WriteFloat('DATA.Weight', SafeElementFloat(rec, 'DATA\Weight'));
  WriteInt('SOUL', SafeElementInt(rec, 'SOUL'));
  WriteInt('SLCP', SafeElementInt(rec, 'SLCP'));
  WriteRecordEnd;
end;

procedure ExportSOUN(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'SOUN');
  WritePath('FNAM.Filename', SafeElementStr(rec, 'FNAM'));
  
  // Sound Data (SNDX or SNDD - xEdit normalizes both)
  if ElementExists(rec, 'SNDX') then begin
    WriteInt('SNDX.MinAttDist', SafeElementInt(rec, 'SNDX\Minimum Attenuation Distance'));
    WriteInt('SNDX.MaxAttDist', SafeElementInt(rec, 'SNDX\Maximum Attenuation Distance'));
    WriteInt('SNDX.FreqAdj', SafeElementInt(rec, 'SNDX\Frequency Adjustment %'));
    WriteInt('SNDX.Flags', SafeElementInt(rec, 'SNDX\Flags'));
    WriteInt('SNDX.Attenuation', SafeElementInt(rec, 'SNDX\Static Attenuation [dB]'));
    WriteInt('SNDX.StopTime', SafeElementInt(rec, 'SNDX\Stop Time'));
    WriteInt('SNDX.StartTime', SafeElementInt(rec, 'SNDX\Start Time'));
  end;
  if ElementExists(rec, 'SNDD') then begin
    WriteInt('SNDD.MinAttDist', SafeElementInt(rec, 'SNDD\Minimum Attenuation Distance'));
    WriteInt('SNDD.MaxAttDist', SafeElementInt(rec, 'SNDD\Maximum Attenuation Distance'));
    WriteInt('SNDD.FreqAdj', SafeElementInt(rec, 'SNDD\Frequency Adjustment %'));
    WriteInt('SNDD.Flags', SafeElementInt(rec, 'SNDD\Flags'));
    WriteInt('SNDD.Attenuation', SafeElementInt(rec, 'SNDD\Static Attenuation [dB]'));
    WriteInt('SNDD.StopTime', SafeElementInt(rec, 'SNDD\Stop Time'));
    WriteInt('SNDD.StartTime', SafeElementInt(rec, 'SNDD\Start Time'));
  end;
  
  WriteRecordEnd;
end;

procedure ExportSPEL(rec: IInterface);
var
  effects, effect: IInterface;
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'SPEL');
  ExportFull(rec);
  WriteInt('SPIT.Type', SafeElementInt(rec, 'SPIT\Type'));
  WriteInt('SPIT.Cost', SafeElementInt(rec, 'SPIT\Cost'));
  WriteInt('SPIT.Level', SafeElementInt(rec, 'SPIT\Level'));
  WriteInt('SPIT.Flags', SafeElementInt(rec, 'SPIT\Flags'));
  // Export first effect type for TES5 Target Type derivation
  effects := ElementByName(rec, 'Effects');
  if Assigned(effects) then begin
    if ElementCount(effects) > 0 then begin
      effect := ElementByIndex(effects, 0);
      WriteInt('FirstEffect.Type', SafeElementInt(effect, 'EFIT\Type'));
    end;
  end;
  ExportEffects(rec);
  WriteRecordEnd;
end;

procedure ExportSTAT(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'STAT');
  ExportModel(rec, 'Model');
  WriteRecordEnd;
end;

procedure ExportTREE(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'TREE');
  ExportModel(rec, 'SPTFile');
  WritePath('LeafTexture', SafeElementStr(rec, 'ICON'));
  WriteFloat('CNAM.LeafCurve', SafeElementFloat(rec, 'CNAM\Leaf Curvature'));
  WriteFloat('CNAM.MinLeafAngle', SafeElementFloat(rec, 'CNAM\Minimum Leaf Angle'));
  WriteFloat('CNAM.MaxLeafAngle', SafeElementFloat(rec, 'CNAM\Maximum Leaf Angle'));
  WriteFloat('CNAM.BranchDim', SafeElementFloat(rec, 'CNAM\Branch Dimming Value'));
  WriteFloat('CNAM.LeafDim', SafeElementFloat(rec, 'CNAM\Leaf Dimming Value'));
  WriteFloat('CNAM.RockSpeed', SafeElementFloat(rec, 'CNAM\Rock Speed'));
  WriteFloat('CNAM.RustleSpeed', SafeElementFloat(rec, 'CNAM\Rustle Speed'));
  WriteFloat('BNAM.Width', SafeElementFloat(rec, 'BNAM\Width'));
  WriteFloat('BNAM.Height', SafeElementFloat(rec, 'BNAM\Height'));
  WriteRecordEnd;
end;

procedure ExportWATR(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'WATR');
  WritePath('TNAM.Texture', SafeElementStr(rec, 'TNAM'));
  WriteInt('ANAM.Opacity', SafeElementInt(rec, 'ANAM'));
  WriteInt('FNAM.Flags', SafeElementInt(rec, 'FNAM'));
  
  if ElementExists(rec, 'DATA') then begin
    WriteFloat('DATA.WindVelocity', SafeElementFloat(rec, 'DATA\Wind Velocity'));
    WriteFloat('DATA.WindDirection', SafeElementFloat(rec, 'DATA\Wind Direction'));
    WriteFloat('DATA.WaveAmp', SafeElementFloat(rec, 'DATA\Wave Amplitude'));
    WriteFloat('DATA.WaveFreq', SafeElementFloat(rec, 'DATA\Wave Frequency'));
    WriteFloat('DATA.SunPower', SafeElementFloat(rec, 'DATA\Sun Power'));
    WriteFloat('DATA.Reflectivity', SafeElementFloat(rec, 'DATA\Reflectivity Amount'));
    WriteFloat('DATA.Fresnel', SafeElementFloat(rec, 'DATA\Fresnel Amount'));
    WriteStr('DATA.ShallowColor', SafeElementStr(rec, 'DATA\Shallow Color'));
    WriteStr('DATA.DeepColor', SafeElementStr(rec, 'DATA\Deep Color'));
    WriteStr('DATA.ReflectionColor', SafeElementStr(rec, 'DATA\Reflection Color'));
    WriteInt('DATA.Damage', SafeElementInt(rec, 'DATA\Damage'));
  end;
  
  WriteRecordEnd;
end;

procedure ExportWEAP(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'WEAP');
  ExportFull(rec);
  ExportModel(rec, 'Model');
  ExportIcon(rec);
  ExportScript(rec);
  ExportEnchantment(rec);
  
  WriteInt('DATA.Type', SafeElementInt(rec, 'DATA\Type'));
  WriteInt('TES5.AnimType', MapWeaponType(SafeElementInt(rec, 'DATA\Type')));
  WriteFloat('DATA.Speed', SafeElementFloat(rec, 'DATA\Speed'));
  WriteFloat('DATA.Reach', SafeElementFloat(rec, 'DATA\Reach'));
  WriteInt('DATA.IgnoresResist', SafeElementInt(rec, 'DATA\Ignores Normal Weapon Resistance'));
  WriteInt('DATA.Value', SafeElementInt(rec, 'DATA\Value'));
  WriteInt('DATA.Health', SafeElementInt(rec, 'DATA\Health'));
  WriteFloat('DATA.Weight', SafeElementFloat(rec, 'DATA\Weight'));
  WriteInt('DATA.Damage', SafeElementInt(rec, 'DATA\Damage'));
  WriteInt('ANAM.EnchantPoints', SafeElementInt(rec, 'ANAM'));
  
  WriteRecordEnd;
end;

procedure ExportWTHR(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'WTHR');
  WritePath('CNAM.CloudLower', SafeElementStr(rec, 'CNAM'));
  WritePath('DNAM.CloudUpper', SafeElementStr(rec, 'DNAM'));
  ExportModel(rec, 'PrecipModel');
  
  // DATA
  if ElementExists(rec, 'DATA') then begin
    WriteInt('DATA.WindSpeed', SafeElementInt(rec, 'DATA\Wind Speed'));
    WriteInt('DATA.CloudSpeedLower', SafeElementInt(rec, 'DATA\Cloud Speed (Lower)'));
    WriteInt('DATA.CloudSpeedUpper', SafeElementInt(rec, 'DATA\Cloud Speed (Upper)'));
    WriteInt('DATA.TransDelta', SafeElementInt(rec, 'DATA\Trans Delta'));
    WriteInt('DATA.SunGlare', SafeElementInt(rec, 'DATA\Sun Glare'));
    WriteInt('DATA.SunDamage', SafeElementInt(rec, 'DATA\Sun Damage'));
    WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA\Flags '));
  end;
  
  // HDR
  if ElementExists(rec, 'HNAM') then begin
    WriteFloat('HNAM.EyeAdaptSpeed', SafeElementFloat(rec, 'HNAM\Eye Adapt Speed'));
    WriteFloat('HNAM.BlurRadius', SafeElementFloat(rec, 'HNAM\Blur Radius'));
    WriteFloat('HNAM.BlurPasses', SafeElementFloat(rec, 'HNAM\Blur Passes'));
    WriteFloat('HNAM.EmissiveMult', SafeElementFloat(rec, 'HNAM\Emissive Mult'));
    WriteFloat('HNAM.TargetLUM', SafeElementFloat(rec, 'HNAM\Target LUM'));
    WriteFloat('HNAM.UpperLUMClamp', SafeElementFloat(rec, 'HNAM\Upper LUM Clamp'));
    WriteFloat('HNAM.BrightScale', SafeElementFloat(rec, 'HNAM\Bright Scale'));
    WriteFloat('HNAM.BrightClamp', SafeElementFloat(rec, 'HNAM\Bright Clamp'));
  end;
  
  WriteRecordEnd;
end;

procedure ExportWRLD(rec: IInterface);
begin
  WriteRecordHeader(rec);
  WritePair('TargetType', 'WRLD');
  ExportFull(rec);
  WriteFormID('WNAM.Parent', SafeLinksTo(ElementBySignature(rec, 'WNAM')));
  WriteFormID('CNAM.Climate', SafeLinksTo(ElementBySignature(rec, 'CNAM')));
  WriteFormID('NAM2.Water', SafeLinksTo(ElementBySignature(rec, 'NAM2')));
  WriteFormID('SNAM.Music', SafeLinksTo(ElementBySignature(rec, 'SNAM')));
  WritePath('ICON.MapImage', SafeElementStr(rec, 'ICON'));
  WriteInt('DATA.Flags', SafeElementInt(rec, 'DATA'));
  // SNAM min object bounds, map offset data
  if ElementExists(rec, 'MNAM') then begin
    WriteInt('MNAM.Width', SafeElementInt(rec, 'MNAM\Usable Dimensions\X'));
    WriteInt('MNAM.Height', SafeElementInt(rec, 'MNAM\Usable Dimensions\Y'));
  end;
  WriteRecordEnd;
end;

//============================================================================
// Main Processing
//============================================================================

procedure ProcessRecord(rec: IInterface);
var
  sig: string;
begin
  try
  sig := Signature(rec);
  
  if sig = 'TES4' then Exit; // Skip file header
  
  // Track parent hierarchy for child record placement
  if sig = 'WRLD' then
    gCurrentWorldFormID := IntToHex(GetLoadOrderFormID(rec), 8)
  else if sig = 'CELL' then begin
    gCurrentCellFormID := IntToHex(GetLoadOrderFormID(rec), 8);
    // Interior cells (flag bit 0) have no parent worldspace
    if (SafeElementInt(rec, 'DATA') and 1) = 1 then
      gCurrentWorldFormID := '';
  end
  else if sig = 'DIAL' then
    gCurrentDialFormID := IntToHex(GetLoadOrderFormID(rec), 8);
  
  if sig = 'ACTI' then ExportACTI(rec)
  else if sig = 'ALCH' then ExportALCH(rec)
  else if sig = 'AMMO' then ExportAMMO(rec)
  else if sig = 'ANIO' then ExportANIO(rec)
  else if sig = 'APPA' then ExportAPPA(rec)
  else if sig = 'ARMO' then ExportARMO(rec)
  else if sig = 'BOOK' then ExportBOOK(rec)
  else if sig = 'BSGN' then ExportBSGN(rec)
  else if sig = 'CELL' then ExportCELL(rec)
  else if sig = 'CLAS' then ExportCLAS(rec)
  else if sig = 'CLMT' then ExportCLMT(rec)
  else if sig = 'CLOT' then ExportCLOT(rec)
  else if sig = 'CONT' then ExportCONT(rec)
  else if sig = 'CREA' then ExportCREA(rec)
  else if sig = 'CSTY' then ExportCSTY(rec)
  else if sig = 'DIAL' then ExportDIAL(rec)
  else if sig = 'DOOR' then ExportDOOR(rec)
  else if sig = 'EFSH' then ExportEFSH(rec)
  else if sig = 'ENCH' then ExportENCH(rec)
  else if sig = 'EYES' then ExportEYES(rec)
  else if sig = 'FACT' then ExportFACT(rec)
  else if sig = 'FLOR' then ExportFLOR(rec)
  else if sig = 'FURN' then ExportFURN(rec)
  else if sig = 'GLOB' then ExportGLOB(rec)
  else if sig = 'GMST' then ExportGMST(rec)
  else if sig = 'GRAS' then ExportGRAS(rec)
  else if sig = 'HAIR' then ExportHAIR(rec)
  else if sig = 'IDLE' then ExportIDLE(rec)
  else if sig = 'INFO' then ExportINFO(rec)
  else if sig = 'INGR' then ExportINGR(rec)
  else if sig = 'KEYM' then ExportKEYM(rec)
  else if sig = 'LAND' then ExportLAND(rec)
  else if sig = 'LIGH' then ExportLIGH(rec)
  else if sig = 'LSCR' then ExportLSCR(rec)
  else if sig = 'LTEX' then ExportLTEX(rec)
  else if sig = 'LVLC' then ExportLVLC(rec)
  else if sig = 'LVLI' then ExportLVLI(rec)
  else if sig = 'LVSP' then ExportLVSP(rec)
  else if sig = 'MGEF' then ExportMGEF(rec)
  else if sig = 'MISC' then ExportMISC(rec)
  else if sig = 'NPC_' then ExportNPC(rec)
  else if sig = 'PACK' then ExportPACK(rec)
  else if sig = 'QUST' then ExportQUST(rec)
  else if sig = 'RACE' then ExportRACE(rec)
  else if sig = 'REFR' then ExportREFR(rec)
  else if sig = 'ACHR' then ExportACHR(rec)
  else if sig = 'ACRE' then ExportACRE(rec)
  else if sig = 'REGN' then ExportREGN(rec)
  else if sig = 'SBSP' then ExportSBSP(rec)
  else if sig = 'SCPT' then ExportSCPT(rec)
  else if sig = 'SGST' then ExportSGST(rec)
  else if sig = 'SKIL' then ExportSKIL(rec)
  else if sig = 'SLGM' then ExportSLGM(rec)
  else if sig = 'SOUN' then ExportSOUN(rec)
  else if sig = 'SPEL' then ExportSPEL(rec)
  else if sig = 'STAT' then ExportSTAT(rec)
  else if sig = 'TREE' then ExportTREE(rec)
  else if sig = 'WATR' then ExportWATR(rec)
  else if sig = 'WEAP' then ExportWEAP(rec)
  else if sig = 'WTHR' then ExportWTHR(rec)
  else if sig = 'WRLD' then ExportWRLD(rec)
  else if sig = 'PGRD' then begin
    // Path grids replaced by NavMesh - skip with note
    WriteRecordHeader(rec);
    WritePair('TargetType', 'PGRD_SKIP');
    WritePair('Note', 'Path grids replaced by NavMesh in TES5 - manual conversion required');
    WriteRecordEnd;
  end
  else if sig = 'ROAD' then begin
    // Roads replaced by NavMesh
    WriteRecordHeader(rec);
    WritePair('TargetType', 'ROAD_SKIP');
    WritePair('Note', 'Roads replaced by NavMesh in TES5');
    WriteRecordEnd;
  end
  else if sig = 'PLYR' then begin
    // Player reference - skip
  end
  else begin
    // Unknown record type - export basic data
    WriteRecordHeader(rec);
    WritePair('TargetType', 'UNKNOWN');
    WritePair('Note', 'Unhandled record type: ' + sig);
    ExportFull(rec);
    WriteRecordEnd;
  end;
  
  Inc(RecordCount);
  if RecordCount mod 1000 = 0 then
    AddMessage('  Exported ' + IntToStr(RecordCount) + ' records...');
  except
    Inc(ErrorCount);
    AddMessage('  WARNING: Skipped record ' + Name(rec) + ' due to error (total skipped: ' + IntToStr(ErrorCount) + ')');
    // Ensure any partial record block is closed so the file stays parseable
    if (slExport.Count > 0) and (slExport[slExport.Count - 1] <> '---RECORD_END---') then begin
      slExport.Add('Note=Record skipped due to export error');
      slExport.Add('---RECORD_END---');
      slExport.Add('');
    end;
  end;
end;

function Initialize: Integer;
var
  settingsFile: string;
  slSettings: TStringList;
begin
  Result := 0;
  SourceFileName := '';
  OutputFileName := 'TES4_Records.txt';
  ExportPath := DataPath + 'TES4Export\';
  gCurrentWorldFormID := '';
  gCurrentCellFormID := '';
  gCurrentDialFormID := '';
  
  // Check for automation settings file
  settingsFile := ScriptsPath + 'conversion_settings.txt';
  if FileExists(settingsFile) then begin
    slSettings := TStringList.Create;
    try
      slSettings.LoadFromFile(settingsFile);
      if slSettings.Values['MODE'] = 'EXPORT' then begin
        if slSettings.Values['SOURCE_FILE'] <> '' then
          SourceFileName := slSettings.Values['SOURCE_FILE'];
        if slSettings.Values['EXPORT_DIR'] <> '' then
          ExportPath := IncludeTrailingBackslash(slSettings.Values['EXPORT_DIR']);
        if slSettings.Values['OUTPUT_FILE'] <> '' then
          OutputFileName := slSettings.Values['OUTPUT_FILE'];
      end;
    finally
      slSettings.Free;
    end;
  end;
  
  ForceDirectories(ExportPath);
  
  slExport := TStringList.Create;
  slExport.Add('# TES4 to TES5 Record Export');
  slExport.Add('# Generated by TES4_Export_Records.pas');
  slExport.Add('# Format: KEY=VALUE pairs between ---RECORD_BEGIN--- / ---RECORD_END--- markers');
  slExport.Add('# FormIDs are in load-order format (first two hex digits = load order index)');
  if SourceFileName <> '' then
    slExport.Add('# Source: ' + SourceFileName);
  slExport.Add('');
  
  RecordCount := 0;
  ErrorCount := 0;
  AddMessage('TES4 Record Export: Starting...');
  if SourceFileName <> '' then
    AddMessage('Source file filter: ' + SourceFileName);
  AddMessage('Export path: ' + ExportPath);
  AddMessage('Output file: ' + OutputFileName);
end;

function Process(e: IInterface): Integer;
begin
  Result := 0;
  // In automation mode, only export records from the specified source file
  if SourceFileName <> '' then begin
    if GetFileName(GetFile(e)) <> SourceFileName then
      Exit;
  end;
  ProcessRecord(e);
end;

function Finalize: Integer;
begin
  Result := 0;
  
  slExport.Add('# Export complete');
  slExport.Add('# Total records: ' + IntToStr(RecordCount));
  
  slExport.SaveToFile(ExportPath + OutputFileName);
  slExport.Free;
  
  AddMessage('TES4 Record Export: Complete!');
  AddMessage('Total records exported: ' + IntToStr(RecordCount));
  if ErrorCount > 0 then
    AddMessage('Records skipped due to errors: ' + IntToStr(ErrorCount));
  AddMessage('Output: ' + ExportPath + OutputFileName);
end;

end.
