{
  TES5_Import_Records.pas
  =======================
  xEdit Pascal Script for TES V (Skyrim) - Import Phase

  Reads the exported TES4 data from TES4_Records.txt and creates
  corresponding records in a Skyrim plugin.

  Usage:
    1. Create a new .esp in TES5Edit (SSEEdit for Skyrim SE)
    2. Select the new plugin
    3. Apply this script
    4. It reads from <DataPath>\TES4Export\TES4_Records.txt
    5. Records are created in the selected plugin

  IMPORTANT NOTES:
  - FormIDs from TES4 cannot be directly reused. This script creates NEW
    records. Cross-references (e.g., a weapon enchantment pointing at an ENCH)
    will need manual relinking after import.
  - Models/meshes/textures need to be manually converted from Oblivion format
    to Skyrim format (NIF version, DDS compression, etc.)
  - TES4 scripts (SCPT) must be manually rewritten in Papyrus.
  - VMAD (Papyrus script attachments) cannot be auto-generated.
  - Some record types have no direct equivalent and are approximated.

  Record Conversion Notes:
  - APPA (Apparatus) -> MISC (no alchemy apparatus in TES5)
  - BSGN (Birthsign) -> spells added to a race or Standing Stone
  - CLOT (Clothing)  -> ARMO with ArmorType = Clothing
  - CREA (Creature)  -> NPC_ (no CREA record in TES5)
  - HAIR (Hair)      -> HDPT (Head Part)  
  - LVLC (Lev.Crea.) -> LVLN (Leveled NPC)
  - SBSP (Subspace)  -> STAT (Static)
  - SGST (Sigil Stone)-> SCRL (Scroll)
  - ACRE (Placed Creature) -> ACHR (Placed NPC)
}
unit TES5_Import_Records;

var
  slImport: TStringList;
  TargetPlugin: IInterface;
  ImportPath: string;
  CurrentLine: Integer;
  RecordCount: Integer;
  SkippedCount: Integer;
  CellFailCount: Integer;
  RefOrphanCount: Integer;
  // FormID map: old TES4 FormID (string) -> new TES5 record
  slFormIDMap: TStringList;
  recData: TStringList;
  // Automation settings
  AutoImportFile: string;
  AutoOutputName: string;
  AutoMappingDir: string;
  AutoMasterMappings: string;
  AutoExportDir: string;
  AutoSkipTypes: string;


//============================================================================
// Utility: Parsing Functions
//============================================================================

function UnescapeStr(s: string): string;
begin
  Result := s;
  Result := StringReplace(Result, '\\', Chr(1), [rfReplaceAll]);
  Result := StringReplace(Result, '\"', '"', [rfReplaceAll]);
  Result := StringReplace(Result, '\r', Chr(13), [rfReplaceAll]);
  Result := StringReplace(Result, '\n', Chr(10), [rfReplaceAll]);
  Result := StringReplace(Result, '\t', Chr(9), [rfReplaceAll]);
  Result := StringReplace(Result, Chr(1), '\', [rfReplaceAll]);
end;

// Read all lines between ---RECORD_BEGIN--- and ---RECORD_END--- into recData
function ReadNextRecord(): Boolean;
var
  line: string;
begin
  Result := False;
  recData.Clear;
  
  // Find next RECORD_BEGIN
  while CurrentLine < slImport.Count do begin
    line := slImport.Strings[CurrentLine];
    Inc(CurrentLine);
    if line = '---RECORD_BEGIN---' then begin
      Result := True;
      Break;
    end;
  end;
  
  if not Result then Exit;
  
  // Read until RECORD_END
  while CurrentLine < slImport.Count do begin
    line := slImport.Strings[CurrentLine];
    Inc(CurrentLine);
    if line = '---RECORD_END---' then
      Break;
    if (line <> '') and (Copy(line, 1, 1) <> '#') then
      recData.Add(line);
  end;
end;

// recData uses KEY=VALUE format; TStringList.Values[] looks up by key natively
function RecordValue(key: string): string;
begin
  Result := recData.Values[key];
end;

function RecordValueInt(key: string): Integer;
begin
  Result := StrToIntDef(recData.Values[key], 0);
end;

function RecordValueFloat(key: string): Double;
begin
  Result := StrToFloatDef(recData.Values[key], 0.0);
end;

function RecordHasKey(key: string): Boolean;
begin
  Result := recData.IndexOfName(key) >= 0;
end;

function MaxInt(a, b: Integer): Integer;
begin
  if a > b then Result := a else Result := b;
end;

//============================================================================
// Record Creation Helpers
//============================================================================

procedure AddOBND(rec: IInterface);
// Add default Object Bounds - required on nearly all TES5 item/object records
begin
  Add(rec, 'OBND', True);
  // Zero bounds is valid - engine just needs the subrecord present
  SetElementNativeValues(rec, 'OBND\X1', 0);
  SetElementNativeValues(rec, 'OBND\Y1', 0);
  SetElementNativeValues(rec, 'OBND\Z1', 0);
  SetElementNativeValues(rec, 'OBND\X2', 0);
  SetElementNativeValues(rec, 'OBND\Y2', 0);
  SetElementNativeValues(rec, 'OBND\Z2', 0);
end;

procedure FinishRecord(rec: IInterface);
// Set form version to 44 (SSE) on every created record
begin
  if Assigned(rec) then
    SetFormVersion(rec, 44);
end;

function GetGroupBySignature(plugin: IInterface; const sig: string): IInterface;
var
  i: Integer;
  grp: IInterface;
begin
  Result := nil;
  for i := 0 to ElementCount(plugin) - 1 do begin
    grp := ElementByIndex(plugin, i);
    if Signature(grp) = 'GRUP' then begin
      if GetElementEditValues(grp, 'Group Type') = '0' then begin
        // Top-level group - check label
        if Pos(sig, Name(grp)) > 0 then begin
          Result := grp;
          Exit;
        end;
      end;
    end;
  end;
end;

function CreateNewRecord(plugin: IInterface; const sig: string): IInterface;
var
  grp: IInterface;
begin
  // Add(plugin, sig, True) finds-or-creates the top-level group in the plugin
  grp := Add(plugin, sig, True);
  if not Assigned(grp) then begin
    Result := nil;
    Exit;
  end;
  // Add(group, sig, True) creates a new record inside that group
  Result := Add(grp, sig, True);
end;

// Look up a FormID in slFormIDMap and find the corresponding record
function FindMappedRecord(const oldFormID: string): IInterface;
var
  newFormIDStr: string;
  newFormID: Cardinal;
  i: Integer;
begin
  Result := nil;
  if oldFormID = '' then Exit;
  newFormIDStr := slFormIDMap.Values[oldFormID];
  if newFormIDStr = '' then Exit;
  newFormID := StrToInt64('$' + newFormIDStr);
  // Search TargetPlugin FIRST to avoid FormID collisions with loaded masters
  // (e.g., our new FormID might match a Dawnguard/Dragonborn record by coincidence)
  Result := RecordByFormID(TargetPlugin, newFormID, True);
  if Assigned(Result) then Exit;
  // Then search other loaded files for records from converted masters
  for i := 0 to FileCount - 1 do begin
    if GetFileName(FileByIndex(i)) <> GetFileName(TargetPlugin) then begin
      Result := RecordByFormID(FileByIndex(i), newFormID, True);
      if Assigned(Result) then Exit;
    end;
  end;
end;

// Create a child record inside a parent record's child group
// Used for REFR/ACHR inside CELL, INFO inside DIAL, LAND inside CELL
function CreateChildRecord(parentRec: IInterface; const childSig: string): IInterface;
var
  childGroup: IInterface;
  parentFile: IInterface;
  overrideRec: IInterface;
begin
  Result := nil;
  if not Assigned(parentRec) then Exit;
  // If parent is in a master file, create an override in our target plugin
  parentFile := GetFile(parentRec);
  if Assigned(parentFile) then begin
    if GetFileName(parentFile) <> GetFileName(TargetPlugin) then begin
      AddMasterIfMissing(TargetPlugin, GetFileName(parentFile));
      overrideRec := wbCopyElementToFile(parentRec, TargetPlugin, False, False);
      if not Assigned(overrideRec) then begin
        AddMessage('  WARNING: Could not create override for ' + Name(parentRec));
        Exit;
      end;
      parentRec := overrideRec;
    end;
  end;
  // Get or create the child group for this parent record
  childGroup := ChildGroup(parentRec);
  if not Assigned(childGroup) then begin
    // Try to create the child group by adding a child record type
    childGroup := Add(parentRec, childSig, True);
    if Assigned(childGroup) then begin
      // If Add returned a record directly, that's our result
      if Signature(childGroup) = childSig then begin
        Result := childGroup;
        Exit;
      end;
      // Add may have created the group itself; try getting child group again
      childGroup := ChildGroup(parentRec);
      if Assigned(childGroup) then begin
        Result := Add(childGroup, childSig, True);
        Exit;
      end;
    end;
    Exit;
  end;
  // Create new record inside the child group
  Result := Add(childGroup, childSig, True);
end;

procedure SetEditorID(rec: IInterface; const edid: string);
begin
  if edid <> '' then begin
    Add(rec, 'EDID', True);
    SetElementEditValues(rec, 'EDID', edid);
  end;
end;

procedure SetFull(rec: IInterface; const name: string);
begin
  if name <> '' then begin
    Add(rec, 'FULL', True);
    SetElementEditValues(rec, 'FULL', UnescapeStr(name));
  end;
end;

procedure SetDescription(rec: IInterface; const desc: string);
begin
  if desc <> '' then begin
    Add(rec, 'DESC', True);
    SetElementEditValues(rec, 'DESC', UnescapeStr(desc));
  end;
end;

procedure SetModel(rec: IInterface; const modelPath: string);
begin
  if modelPath <> '' then begin
    Add(rec, 'Model', True);
    SetElementEditValues(rec, 'Model\MODL', modelPath);
  end;
end;

procedure SetIcon(rec: IInterface);
var
  icon: string;
begin
  icon := RecordValue('ICON');
  if icon <> '' then begin
    // TES5 uses ICON inside an unnamed struct for most records
    Add(rec, 'ICON', True);
    SetElementEditValues(rec, 'ICON', icon);
  end;
end;

procedure StoreFormIDMapping(const oldFormID: string; newRec: IInterface);
begin
  if (oldFormID <> '') and Assigned(newRec) then
    slFormIDMap.Values[oldFormID] := IntToHex(GetLoadOrderFormID(newRec), 8);
end;

//============================================================================
// Actor Value Mapping (TES4 index -> TES5 index)
//============================================================================

function MapTES4ActorValueToTES5(tes4AV: Integer): Integer;
begin
  case tes4AV of
    8:  Result := 24;  // Health
    9:  Result := 25;  // Magicka
    10: Result := 26;  // Stamina (Fatigue)
    15: Result := 9;   // Block
    18: Result := 10;  // Heavy Armor
    27: Result := 12;  // Light Armor
    31: Result := 15;  // Sneak
    19: Result := 16;  // Alchemy
    32: Result := 17;  // Speech (Speechcraft)
    20: Result := 18;  // Alteration
    21: Result := 19;  // Conjuration
    22: Result := 20;  // Destruction
    23: Result := 21;  // Illusion
    25: Result := 22;  // Restoration
    28: Result := 8;   // Archery (Marksman)
    14: Result := 7;   // One-Handed (Blade)
    16: Result := 7;   // One-Handed (Blunt)
    17: Result := 7;   // One-Handed (Hand to Hand)
    12: Result := 11;  // Smithing (Armorer)
    30: Result := 13;  // Lockpicking (Security)
    29: Result := 14;  // Pickpocket (Mercantile)
    24: Result := 21;  // Illusion (Mysticism)
  else
    Result := -1;
  end;
end;

//============================================================================
// Skill Mapping for RACE skill boosts
//============================================================================

function MapTES4SkillToTES5(tes4Skill: Integer): Integer;
begin
  // TES4 skill indices (from wbMajorSkillEnum):
  // 12=Armorer,13=Athletics,14=Blade,15=Block,16=Blunt,17=HandToHand,
  // 18=HeavyArmor,19=Alchemy,20=Alteration,21=Conjuration,22=Destruction,
  // 23=Illusion,24=Mysticism,25=Restoration,26=Acrobatics,27=LightArmor,
  // 28=Marksman,29=Mercantile,30=Security,31=Sneak,32=Speechcraft
  // TES5 skills (from wbSkillEnum):
  // 6=OneHanded,7=TwoHanded,8=Archery,9=Block,10=Smithing,11=HeavyArmor,
  // 12=LightArmor,13=Pickpocket,14=Lockpicking,15=Sneak,16=Alchemy,
  // 17=Speech,18=Alteration,19=Conjuration,20=Destruction,21=Illusion,
  // 22=Restoration,23=Enchanting
  case tes4Skill of
    12: Result := 10;  // Armorer -> Smithing
    13: Result := -1;  // Athletics -> None
    14: Result := 6;   // Blade -> One Handed
    15: Result := 9;   // Block -> Block
    16: Result := 6;   // Blunt -> One Handed
    17: Result := 6;   // Hand to Hand -> One Handed
    18: Result := 11;  // Heavy Armor -> Heavy Armor
    19: Result := 16;  // Alchemy -> Alchemy
    20: Result := 18;  // Alteration -> Alteration
    21: Result := 19;  // Conjuration -> Conjuration
    22: Result := 20;  // Destruction -> Destruction
    23: Result := 21;  // Illusion -> Illusion
    24: Result := 21;  // Mysticism -> Illusion
    25: Result := 22;  // Restoration -> Restoration
    26: Result := -1;  // Acrobatics -> None
    27: Result := 12;  // Light Armor -> Light Armor
    28: Result := 8;   // Marksman -> Archery
    29: Result := 13;  // Mercantile -> Pickpocket
    30: Result := 14;  // Security -> Lockpicking
    31: Result := 15;  // Sneak -> Sneak
    32: Result := 17;  // Speechcraft -> Speech
  else
    Result := -1;
  end;
end;

//============================================================================
// Magic Effect Conversion
//============================================================================
// TES4 uses 4-char MGEF codes; TES5 uses FormID-based MGEF references.
// This is a best-effort mapping for common effects.

function MapMGEFCode(const code: string): string;
begin
  // Returns the TES5 EditorID equivalent (user must manually resolve FormIDs)
  // Common Oblivion effects -> Skyrim equivalents
  if code = 'REFA' then Result := 'AbRestoreHealth'      // Restore Health
  else if code = 'REHE' then Result := 'AbRestoreHealth'
  else if code = 'REMA' then Result := 'AbRestoreMagicka'  // Restore Magicka
  else if code = 'REFA' then Result := 'AbRestoreStamina'  // Restore Fatigue -> Stamina
  else if code = 'DRHE' then Result := 'AbDamageHealth'    // Drain Health
  else if code = 'DRMA' then Result := 'AbDamageMagicka'
  else if code = 'DRFA' then Result := 'AbDamageStamina'
  else if code = 'FOHE' then Result := 'AbFortifyHealth'   // Fortify Health
  else if code = 'FOMA' then Result := 'AbFortifyMagicka'
  else if code = 'FOFA' then Result := 'AbFortifyStamina'
  else if code = 'RSFI' then Result := 'AbResistFire'
  else if code = 'RSFR' then Result := 'AbResistFrost'
  else if code = 'RSSH' then Result := 'AbResistShock'
  else if code = 'RSPO' then Result := 'AbResistPoison'
  else if code = 'RSMA' then Result := 'AbResistMagic'
  else if code = 'RSDI' then Result := 'AbResistDisease'
  else if code = 'INVI' then Result := 'InvisibilityFFSelf'
  else if code = 'CHML' then Result := 'InvisibilityFFSelf' // Chameleon -> Invis
  else if code = 'PARA' then Result := 'ParalysisFFContact'
  else if code = 'SLNC' then Result := 'AbSilence'
  else if code = 'WABR' then Result := 'AbWaterbreathing'
  else if code = 'WAWA' then Result := 'AbWaterwalking'
  else Result := ''; // Unknown - needs manual mapping
end;

//============================================================================
// Import Functions for Each Record Type
//============================================================================

procedure ImportACTI();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'ACTI');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportALCH();
var
  rec: IInterface;
  value, flags, skFlags: Integer;
  weight: Double;
  fullName: string;
begin
  rec := CreateNewRecord(TargetPlugin, 'ALCH');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  SetDescription(rec, RecordValue('DESC'));
  
  // ENIT data - per Skyblivion flag/keyword handling
  flags := RecordValueInt('ENIT.Flags');
  skFlags := 0;
  if (flags and 1) <> 0 then skFlags := skFlags or 1; // No Auto Calc
  
  Add(rec, 'ENIT', True);
  value := RecordValueInt('ENIT.Value');
  SetElementNativeValues(rec, 'ENIT\Value', value);
  
  // Food detection per Skyblivion
  if (flags and 2) <> 0 then begin
    skFlags := skFlags or 2; // Food Item flag
    // Food use sound: ITMFoodEat [SNDR:000CAF94]
    SetElementEditValues(rec, 'ENIT\Sound - Consume', '000CAF94');
  end;
  
  // Poison detection per Skyblivion: check name for 'poison'
  fullName := RecordValue('FULL');
  if Pos('poison', LowerCase(fullName)) > 0 then begin
    skFlags := skFlags or $20000; // Poison flag
    // Poison use sound: ITMPoisonUse [SNDR:00106614]
    SetElementEditValues(rec, 'ENIT\Sound - Consume', '00106614');
  end;
  
  SetElementNativeValues(rec, 'ENIT\Flags', skFlags);
  
  weight := RecordValueFloat('Weight');
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Weight', weight);
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportAMMO();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'AMMO');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  SetDescription(rec, RecordValue('DESC'));
  
  // DATA - TES5 AMMO DATA structure
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Damage', RecordValueInt('DATA.Damage'));
  
  // DNAM - projectile and other data
  Add(rec, 'DNAM', True);
  SetElementNativeValues(rec, 'DNAM\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DNAM\Weight', RecordValueFloat('DATA.Weight'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportARMO();
var
  rec, armaRec, armaGrp, armaModl: IInterface;
  bipedSlots, armorType: Integer;
  maleModel, femaleModel, edid: string;
begin
  rec := CreateNewRecord(TargetPlugin, 'ARMO');
  if not Assigned(rec) then Exit;
  
  edid := RecordValue('EditorID');
  SetEditorID(rec, edid);
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetDescription(rec, RecordValue('DESC'));
  
  // BOD2 - Body Template (TES5 uses BOD2 instead of BMDT)
  bipedSlots := RecordValueInt('TES5.BipedSlots');
  armorType := RecordValueInt('ArmorType');
  
  Add(rec, 'BOD2', True);
  SetElementNativeValues(rec, 'BOD2\First Person Flags', bipedSlots);
  SetElementNativeValues(rec, 'BOD2\Armor Type', armorType);
  
  // RNAM - Race (DefaultRace 00000019)
  Add(rec, 'RNAM', True);
  SetElementEditValues(rec, 'RNAM', '00000019');
  
  // Male world model (on ARMO for inventory display)
  maleModel := RecordValue('Male.WorldModel');
  if maleModel <> '' then begin
    Add(rec, 'Male world model', True);
    SetElementEditValues(rec, 'Male world model\MOD2', maleModel);
  end;
  
  // Female world model  
  femaleModel := RecordValue('Female.WorldModel');
  if femaleModel <> '' then begin
    Add(rec, 'Female world model', True);
    SetElementEditValues(rec, 'Female world model\MOD4', femaleModel);
  end;
  
  // DATA
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  // DNAM (Armor Rating in TES5)
  Add(rec, 'DNAM', True);
  SetElementNativeValues(rec, 'DNAM', RecordValueInt('DATA.Armor'));
  
  // Create companion ARMA record (armor models must be on ARMA in TES5)
  armaRec := CreateNewRecord(TargetPlugin, 'ARMA');
  if Assigned(armaRec) then begin
    SetEditorID(armaRec, edid + 'AA');
    AddOBND(armaRec);
    
    Add(armaRec, 'BOD2', True);
    SetElementNativeValues(armaRec, 'BOD2\First Person Flags', bipedSlots);
    SetElementNativeValues(armaRec, 'BOD2\Armor Type', armorType);
    
    // RNAM on ARMA
    Add(armaRec, 'RNAM', True);
    SetElementEditValues(armaRec, 'RNAM', '00000019');
    
    // DNAM (priority/weight slider)
    Add(armaRec, 'DNAM', True);
    
    // Male biped model ? ARMA MOD2
    if RecordValue('Male.BipedModel') <> '' then begin
      Add(armaRec, 'Male world model', True);
      SetElementEditValues(armaRec, 'Male world model\MOD2', RecordValue('Male.BipedModel'));
    end;
    
    // Female biped model ? ARMA MOD3
    if RecordValue('Female.BipedModel') <> '' then begin
      Add(armaRec, 'Female world model', True);
      SetElementEditValues(armaRec, 'Female world model\MOD3', RecordValue('Female.BipedModel'));
    end;
    
    FinishRecord(armaRec);
    
    // Link ARMO to ARMA via Armature array
    Add(rec, 'Armature', True);
    armaModl := ElementByName(rec, 'Armature');
    if Assigned(armaModl) then begin
      armaModl := ElementAssign(armaModl, HighInteger, nil, False);
      if Assigned(armaModl) then
        SetEditValue(armaModl, IntToHex(GetLoadOrderFormID(armaRec), 8));
    end;
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportBOOK();
var
  rec: IInterface;
  flags, teaches: Integer;
  tes5Skill: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'BOOK');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  SetDescription(rec, RecordValue('DESC'));
  
  // DATA
  flags := RecordValueInt('DATA.Flags');
  teaches := RecordValueInt('DATA.Teaches');
  
  Add(rec, 'DATA', True);
  if teaches >= 0 then begin
    tes5Skill := MapTES4SkillToTES5(teaches + 12);
    if tes5Skill >= 0 then begin
      SetElementNativeValues(rec, 'DATA\Flags', 1);  // Teaches skill
      SetElementNativeValues(rec, 'DATA\Teaches', tes5Skill);
    end;
  end;
  
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportCELL();
var
  rec, parentWorld, parentFile: IInterface;
  parentWorldID, cellSpec: string;
  tes4Flags, tes5Flags, gridX, gridY: Integer;
  isInterior, hasGrid, createdInWorld: Boolean;
begin
  parentWorldID := RecordValue('ParentWRLD');
  tes4Flags := RecordValueInt('DATA.Flags');
  isInterior := (tes4Flags and 1) = 1;
  hasGrid := RecordHasKey('XCLC.X');
  createdInWorld := False;
  
  // Clean cell flags per Skyblivion:
  // Remove 'Oblivion interior' flag (bit 3, $08) from interior cells
  tes5Flags := tes4Flags;
  if isInterior then
    tes5Flags := tes5Flags and $FFF7; // Clear bit 3
  // Clear 'hand changed' flag (bit 6, $40)
  tes5Flags := tes5Flags and $FFBF;
  
  rec := nil;
  
  if (not isInterior) and (parentWorldID <> '') then begin
    parentWorld := FindMappedRecord(parentWorldID);
    if Assigned(parentWorld) then begin
      parentFile := GetFile(parentWorld);
      if Assigned(parentFile) then begin
        if GetFileName(parentFile) <> GetFileName(TargetPlugin) then begin
          AddMasterIfMissing(TargetPlugin, GetFileName(parentFile));
          parentWorld := wbCopyElementToFile(parentWorld, TargetPlugin, False, False);
        end;
      end;
      
      if Assigned(parentWorld) then begin
        if hasGrid then begin
          gridX := RecordValueInt('XCLC.X');
          gridY := RecordValueInt('XCLC.Y');
          cellSpec := 'CELL[' + IntToStr(gridX) + ',' + IntToStr(gridY) + ']';
        end else begin
          cellSpec := 'CELL[P]';
        end;
        rec := Add(parentWorld, cellSpec, True);
        if Assigned(rec) then
          createdInWorld := True;
      end;
    end;
    
    if not Assigned(rec) then begin
      Inc(CellFailCount);
      if CellFailCount <= 10 then
        AddMessage('  NOTE: CELL ' + RecordValue('FormID') + ' - parent WRLD ' + parentWorldID + ' not found, creating as top-level');
      rec := CreateNewRecord(TargetPlugin, 'CELL');
    end;
  end else begin
    rec := CreateNewRecord(TargetPlugin, 'CELL');
  end;
  
  if not Assigned(rec) then begin
    Inc(CellFailCount);
    if CellFailCount <= 10 then
      AddMessage('  WARNING: Failed to create CELL ' + RecordValue('FormID'));
    Inc(SkippedCount);
    Exit;
  end;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  // DATA - TES5 uses U16 flags
  if not createdInWorld then begin
    Add(rec, 'DATA', True);
    SetElementNativeValues(rec, 'DATA', tes5Flags);
  end;
  
  // Lighting (interior cells)
  if RecordHasKey('XCLL.Ambient') then begin
    Add(rec, 'XCLL', True);
    SetElementEditValues(rec, 'XCLL\Ambient Color', RecordValue('XCLL.Ambient'));
    SetElementEditValues(rec, 'XCLL\Directional Color', RecordValue('XCLL.Directional'));
    // TES5 has separate Fog Near/Far colors; TES4 has single Fog Color
    // Set both to the same per Skyblivion approach
    SetElementEditValues(rec, 'XCLL\Fog Color Near', RecordValue('XCLL.Fog'));
    SetElementEditValues(rec, 'XCLL\Fog Color Far', RecordValue('XCLL.Fog'));
    SetElementNativeValues(rec, 'XCLL\Fog Near', RecordValueFloat('XCLL.FogNear'));
    SetElementNativeValues(rec, 'XCLL\Fog Far', RecordValueFloat('XCLL.FogFar'));
    SetElementNativeValues(rec, 'XCLL\Directional Rotation XY', RecordValueInt('XCLL.DirRotXY'));
    SetElementNativeValues(rec, 'XCLL\Directional Rotation Z', RecordValueInt('XCLL.DirRotZ'));
    SetElementNativeValues(rec, 'XCLL\Directional Fade', RecordValueFloat('XCLL.DirFade'));
    SetElementNativeValues(rec, 'XCLL\Fog Clip Dist', RecordValueFloat('XCLL.FogClip'));
  end;
  
  // Grid (exterior cells) - do NOT set on worldspace cells
  if hasGrid and (not createdInWorld) then begin
    Add(rec, 'XCLC', True);
    SetElementNativeValues(rec, 'XCLC\X', RecordValueInt('XCLC.X'));
    SetElementNativeValues(rec, 'XCLC\Y', RecordValueInt('XCLC.Y'));
  end;
  
  // Water height
  if RecordHasKey('XCLW') then begin
    Add(rec, 'XCLW', True);
    SetElementNativeValues(rec, 'XCLW', RecordValueFloat('XCLW'));
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;
  
  // Lighting (interior cells)
  if RecordHasKey('XCLL.Ambient') then begin
    Add(rec, 'XCLL', True);
    SetElementEditValues(rec, 'XCLL\Ambient Color', RecordValue('XCLL.Ambient'));
    SetElementEditValues(rec, 'XCLL\Directional Color', RecordValue('XCLL.Directional'));
    SetElementEditValues(rec, 'XCLL\Fog Color Near', RecordValue('XCLL.Fog'));
    SetElementNativeValues(rec, 'XCLL\Fog Near', RecordValueFloat('XCLL.FogNear'));
    SetElementNativeValues(rec, 'XCLL\Fog Far', RecordValueFloat('XCLL.FogFar'));
    SetElementNativeValues(rec, 'XCLL\Directional Rotation XY', RecordValueInt('XCLL.DirRotXY'));
    SetElementNativeValues(rec, 'XCLL\Directional Rotation Z', RecordValueInt('XCLL.DirRotZ'));
    SetElementNativeValues(rec, 'XCLL\Directional Fade', RecordValueFloat('XCLL.DirFade'));
    SetElementNativeValues(rec, 'XCLL\Fog Clip Dist', RecordValueFloat('XCLL.FogClip'));
  end;
  
  // Grid (exterior cells)
  // Do NOT set XCLC on cells created via CELL[x,y] - they are already positioned
  // correctly, and setting XCLC triggers UpdateCellChildGroup which throws
  // "Could not determine grid cell" if the cell is in a worldspace block structure
  if hasGrid and (not createdInWorld) then begin
    Add(rec, 'XCLC', True);
    SetElementNativeValues(rec, 'XCLC\X', RecordValueInt('XCLC.X'));
    SetElementNativeValues(rec, 'XCLC\Y', RecordValueInt('XCLC.Y'));
  end;
  
  // Water height
  if RecordHasKey('XCLW') then begin
    Add(rec, 'XCLW', True);
    SetElementNativeValues(rec, 'XCLW', RecordValueFloat('XCLW'));
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportCLAS();
var
  rec: IInterface;
  spec, attr, skill, i: Integer;
  // Skill weight accumulators (18 skills + 3 attributes)
  w: array[0..20] of Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'CLAS');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetDescription(rec, RecordValue('DESC'));
  
  // Initialize weights to 0
  for i := 0 to 20 do w[i] := 0;
  // Indices: 0=1H,1=2H,2=Archery,3=Block,4=Smithing,5=Heavy,6=Light,
  //   7=Pickpocket,8=Lockpick,9=Sneak,10=Alchemy,11=Speech,12=Alt,
  //   13=Conj,14=Dest,15=Illusion,16=Rest,17=Ench,18=Health,19=Magicka,20=Stamina
  
  // Specialization mapping per Skyblivion
  spec := RecordValueInt('DATA.Specialization');
  case spec of
    0: begin // Combat
      w[0] := w[0]+1; w[1] := w[1]+1; w[3] := w[3]+1; w[4] := w[4]+1;
      w[5] := w[5]+1; w[6] := w[6]+1;
      w[18] := w[18]+1; w[20] := w[20]+1;
    end;
    1: begin // Magic
      w[12] := w[12]+1; w[13] := w[13]+1; w[14] := w[14]+1;
      w[15] := w[15]+1; w[16] := w[16]+1; w[17] := w[17]+1;
      w[19] := w[19]+1;
    end;
    2: begin // Stealth
      w[0] := w[0]+1; w[2] := w[2]+1; w[6] := w[6]+1; w[7] := w[7]+1;
      w[8] := w[8]+1; w[9] := w[9]+1; w[10] := w[10]+1; w[11] := w[11]+1;
      w[18] := w[18]+1; w[20] := w[20]+1;
    end;
  end;
  
  // Two primary attributes per Skyblivion mapping
  for i := 0 to 1 do begin
    if i = 0 then attr := RecordValueInt('DATA.Attr1')
    else attr := RecordValueInt('DATA.Attr2');
    case attr of
      0: begin w[18]:=w[18]+2; w[20]:=w[20]+1; w[0]:=w[0]+1; w[1]:=w[1]+1; end; // Strength
      1: begin w[19]:=w[19]+2; w[10]:=w[10]+1; w[13]:=w[13]+1; w[17]:=w[17]+1; end; // Intelligence
      2: begin w[19]:=w[19]+2; w[20]:=w[20]+1; w[12]:=w[12]+1; w[14]:=w[14]+1; w[16]:=w[16]+1; end; // Willpower
      3: begin w[18]:=w[18]+1; w[20]:=w[20]+1; w[2]:=w[2]+1; w[7]:=w[7]+1; w[8]:=w[8]+1; w[9]:=w[9]+1; end; // Agility
      4: begin w[20]:=w[20]+2; w[6]:=w[6]+1; w[7]:=w[7]+1; w[8]:=w[8]+1; w[9]:=w[9]+1; end; // Speed
      5: begin w[18]:=w[18]+1; w[20]:=w[20]+2; w[3]:=w[3]+1; w[4]:=w[4]+1; w[5]:=w[5]+1; end; // Endurance
      6: begin w[19]:=w[19]+2; w[11]:=w[11]+1; w[15]:=w[15]+1; end; // Personality
      7: begin w[18]:=w[18]+1; w[19]:=w[19]+1; w[20]:=w[20]+1; w[0]:=w[0]+1; w[1]:=w[1]+1; w[10]:=w[10]+1; w[17]:=w[17]+1; w[9]:=w[9]+1; end; // Luck
    end;
  end;
  
  // Seven major skills per Skyblivion mapping
  for i := 0 to 6 do begin
    skill := RecordValueInt('DATA.MajorSkill[' + IntToStr(i) + ']');
    case skill of
      12: w[4] := w[4]+1;             // Armorer -> Smithing
      13: w[5] := w[5]+1;             // Athletics -> Heavy Armor
      14: begin w[0]:=w[0]+1; w[1]:=w[1]+1; end; // Blade -> 1H+2H
      15: w[3] := w[3]+1;             // Block
      16: begin w[0]:=w[0]+1; w[1]:=w[1]+1; end; // Blunt -> 1H+2H
      17: begin w[0]:=w[0]+1; w[1]:=w[1]+1; end; // Hand to Hand -> 1H+2H
      18: w[5] := w[5]+1;             // Heavy Armor
      19: w[10] := w[10]+1;           // Alchemy
      20: w[12] := w[12]+1;           // Alteration
      21: w[13] := w[13]+1;           // Conjuration
      22: w[14] := w[14]+1;           // Destruction
      23: w[15] := w[15]+1;           // Illusion
      24: begin w[12]:=w[12]+1; w[16]:=w[16]+1; end; // Mysticism -> Alt+Rest
      25: w[16] := w[16]+1;           // Restoration
      26: w[6] := w[6]+1;             // Acrobatics -> Light Armor
      27: w[6] := w[6]+1;             // Light Armor
      28: w[2] := w[2]+1;             // Marksman -> Archery
      29: w[11] := w[11]+1;           // Mercantile -> Speech
      30: begin w[7]:=w[7]+1; w[8]:=w[8]+1; end; // Security -> Pick+Lock
      31: w[9] := w[9]+1;             // Sneak
      32: w[11] := w[11]+1;           // Speechcraft -> Speech
    end;
  end;
  
  // TES5 CLAS DATA
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Flags', RecordValueInt('DATA.Flags'));
  SetElementNativeValues(rec, 'DATA\Teaches',
    MapTES4ActorValueToTES5(RecordValueInt('DATA.Teaches')));
  SetElementNativeValues(rec, 'DATA\Maximum Training Level',
    RecordValueInt('DATA.MaxTraining'));
  
  // Set computed skill weights
  SetElementNativeValues(rec, 'DATA\Skill Weights\One Handed', w[0]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Two Handed', w[1]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Archery', w[2]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Block', w[3]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Smithing', w[4]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Heavy Armor', w[5]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Light Armor', w[6]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Pickpocket', w[7]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Lockpicking', w[8]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Sneak', w[9]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Alchemy', w[10]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Speech', w[11]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Alteration', w[12]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Conjuration', w[13]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Destruction', w[14]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Illusion', w[15]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Restoration', w[16]);
  SetElementNativeValues(rec, 'DATA\Skill Weights\Enchanting', w[17]);
  SetElementNativeValues(rec, 'DATA\Attribute Weights\Health', w[18]);
  SetElementNativeValues(rec, 'DATA\Attribute Weights\Magicka', w[19]);
  SetElementNativeValues(rec, 'DATA\Attribute Weights\Stamina', w[20]);
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportCLMT();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'CLMT');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // Weather types need FormID resolution
  // Sun texture
  if RecordValue('SunTexture') <> '' then begin
    Add(rec, 'FNAM', True);
    SetElementEditValues(rec, 'FNAM', RecordValue('SunTexture'));
  end;
  if RecordValue('SunGlare') <> '' then begin
    Add(rec, 'GNAM', True);
    SetElementEditValues(rec, 'GNAM', RecordValue('SunGlare'));
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportCONT();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'CONT');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  // DATA
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Flags', RecordValueInt('DATA.Flags'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportNPC();
var
  rec: IInterface;
  flags, skFlags: Integer;
  origType: string;
  health, magicka, stamina: Integer;
  aggression, confidence, responsibility: Integer;
  obLevel, obCalcMin, obCalcMax: Integer;
  obStr, obInt, obEnd: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'NPC_');
  if not Assigned(rec) then Exit;
  
  origType := RecordValue('OriginalType');
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  
  // ACBS Configuration - filter to only compatible flags per Skyblivion
  Add(rec, 'ACBS', True);
  flags := RecordValueInt('ACBS.Flags');
  
  if origType = 'CREA' then begin
    // Creatures: keep essential, respawn, pcmult, summonable; force autocalc
    skFlags := flags and ($02 + $08 + $80 + $4000);
    // No Blood Spray ($800) -> Doesn't Bleed ($10000)
    if (flags and $800) <> 0 then skFlags := skFlags or $10000;
    skFlags := skFlags or $10; // Force auto-calc
  end else begin
    // NPCs: keep female, essential, respawn, autocalc, pcmult, summonable
    skFlags := flags and ($01 + $02 + $08 + $10 + $80 + $4000);
  end;
  SetElementNativeValues(rec, 'ACBS\Flags', skFlags);
  
  // Level conversion per Skyblivion: PC level offset uses multiplier formula
  obLevel := RecordValueInt('ACBS.Level');
  if (flags and $80) <> 0 then begin
    // PC Level Mult mode: convert offset to multiplier
    SetElementEditValues(rec, 'ACBS\Level Mult', FloatToStr(1.0 + obLevel / 20.0));
  end else begin
    SetElementNativeValues(rec, 'ACBS\Level', obLevel);
  end;
  
  // CalcMin/Max: double values per Skyblivion
  obCalcMin := RecordValueInt('ACBS.CalcMin') * 2;
  obCalcMax := RecordValueInt('ACBS.CalcMax') * 2;
  if obCalcMax = 0 then obCalcMax := 100;
  SetElementNativeValues(rec, 'ACBS\Calc min level', obCalcMin);
  SetElementNativeValues(rec, 'ACBS\Calc max level', obCalcMax);
  
  // Health/Magicka/Stamina: per Skyblivion approach
  // DNAM stores the base values, ACBS offsets store attribute-derived bonuses
  if origType = 'CREA' then begin
    health := RecordValueInt('DATA.Health');
    magicka := RecordValueInt('ACBS.SpellPoints');
    stamina := RecordValueInt('ACBS.Fatigue');
    obStr := RecordValueInt('DATA.Strength');
    obInt := RecordValueInt('DATA.Intelligence');
    obEnd := RecordValueInt('DATA.Endurance');
  end else begin
    health := RecordValueInt('DATA.Health');
    magicka := RecordValueInt('DATA.Intelligence') * 2;
    stamina := RecordValueInt('DATA.Endurance') + RecordValueInt('DATA.Agility');
    obStr := RecordValueInt('DATA.Strength');
    obInt := RecordValueInt('DATA.Intelligence');
    obEnd := RecordValueInt('DATA.Endurance');
  end;
  
  // Skyblivion: ACBS offsets = attribute values, DNAM = actual base stats
  SetElementNativeValues(rec, 'ACBS\Health Offset', obEnd);
  SetElementNativeValues(rec, 'ACBS\Magicka Offset', obInt);
  SetElementNativeValues(rec, 'ACBS\Stamina Offset', obStr);
  SetElementNativeValues(rec, 'ACBS\Speed Multiplier', 100);
  SetElementNativeValues(rec, 'ACBS\Disposition Base', 35);
  SetElementNativeValues(rec, 'ACBS\Barter gold', RecordValueInt('ACBS.BarterGold'));
  
  // AI Data - thresholds matched to Skyblivion
  Add(rec, 'AIDT', True);
  aggression := RecordValueInt('AIDT.Aggression');
  if aggression < 40 then
    SetElementNativeValues(rec, 'AIDT\Aggression', 0)  // Unaggressive
  else if aggression < 70 then
    SetElementNativeValues(rec, 'AIDT\Aggression', 1)  // Aggressive
  else
    SetElementNativeValues(rec, 'AIDT\Aggression', 2); // Very Aggressive
  
  confidence := RecordValueInt('AIDT.Confidence');
  if confidence < 20 then
    SetElementNativeValues(rec, 'AIDT\Confidence', 0)  // Cowardly
  else if confidence < 40 then
    SetElementNativeValues(rec, 'AIDT\Confidence', 1)  // Cautious
  else if confidence < 60 then
    SetElementNativeValues(rec, 'AIDT\Confidence', 2)  // Average
  else if confidence < 80 then
    SetElementNativeValues(rec, 'AIDT\Confidence', 3)  // Brave
  else
    SetElementNativeValues(rec, 'AIDT\Confidence', 4); // Foolhardy
  
  SetElementNativeValues(rec, 'AIDT\Energy Level', RecordValueInt('AIDT.EnergyLevel'));
  SetElementNativeValues(rec, 'AIDT\Mood', 0); // Neutral
  
  // Responsibility per Skyblivion: <30 = No crime, 30+ = Any crime
  responsibility := RecordValueInt('AIDT.Responsibility');
  if responsibility < 30 then begin
    SetElementNativeValues(rec, 'AIDT\Responsibility', 3); // No crime
    SetElementNativeValues(rec, 'AIDT\Assistance', 1);     // Helps Allies
  end else begin
    SetElementNativeValues(rec, 'AIDT\Responsibility', 0); // Any crime
    SetElementNativeValues(rec, 'AIDT\Assistance', 2);     // Helps Friends and Allies
  end;
  
  // NPC_ DATA in TES5 is a zero-length marker
  Add(rec, 'DATA', True);
  
  // DNAM - TES5 skills and stats per Skyblivion mapping
  Add(rec, 'DNAM', True);
  if origType = 'CREA' then begin
    // Creatures have aggregate skills: Combat/Magic/Stealth
    // Combat skills
    SetElementNativeValues(rec, 'DNAM\Skill Values\OneHanded', RecordValueInt('DATA.CombatSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\TwoHanded', RecordValueInt('DATA.CombatSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Marksman', RecordValueInt('DATA.CombatSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Block', RecordValueInt('DATA.CombatSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\HeavyArmor', RecordValueInt('DATA.CombatSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\LightArmor', RecordValueInt('DATA.CombatSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Smithing', RecordValueInt('DATA.CombatSkill'));
    // Magic skills
    SetElementNativeValues(rec, 'DNAM\Skill Values\Alchemy', RecordValueInt('DATA.MagicSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Alteration', RecordValueInt('DATA.MagicSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Conjuration', RecordValueInt('DATA.MagicSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Destruction', RecordValueInt('DATA.MagicSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Illusion', RecordValueInt('DATA.MagicSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Restoration', RecordValueInt('DATA.MagicSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Enchanting', RecordValueInt('DATA.MagicSkill'));
    // Stealth skills
    SetElementNativeValues(rec, 'DNAM\Skill Values\Pickpocket', RecordValueInt('DATA.StealthSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Lockpicking', RecordValueInt('DATA.StealthSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Sneak', RecordValueInt('DATA.StealthSkill'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Speechcraft', RecordValueInt('DATA.StealthSkill'));
  end else begin
    // NPCs have individual skills - Skyblivion mapping
    SetElementNativeValues(rec, 'DNAM\Skill Values\OneHanded', MaxInt(RecordValueInt('DATA.Blade'), RecordValueInt('DATA.Blunt')));
    SetElementNativeValues(rec, 'DNAM\Skill Values\TwoHanded', MaxInt(MaxInt(RecordValueInt('DATA.HandToHand'), RecordValueInt('DATA.Blade')), RecordValueInt('DATA.Blunt')));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Marksman', RecordValueInt('DATA.Marksman'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Block', RecordValueInt('DATA.Block'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Smithing', RecordValueInt('DATA.Armorer'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\HeavyArmor', MaxInt(RecordValueInt('DATA.HeavyArmor'), RecordValueInt('DATA.Athletics')));
    SetElementNativeValues(rec, 'DNAM\Skill Values\LightArmor', MaxInt(RecordValueInt('DATA.LightArmor'), RecordValueInt('DATA.Acrobatics')));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Pickpocket', RecordValueInt('DATA.Sneak'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Lockpicking', RecordValueInt('DATA.Security'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Sneak', RecordValueInt('DATA.Sneak'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Alchemy', RecordValueInt('DATA.Alchemy'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Speechcraft', MaxInt(RecordValueInt('DATA.Speechcraft'), RecordValueInt('DATA.Mercantile')));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Alteration', RecordValueInt('DATA.Alteration'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Conjuration', RecordValueInt('DATA.Conjuration'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Destruction', RecordValueInt('DATA.Destruction'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Illusion', MaxInt(RecordValueInt('DATA.Illusion'), RecordValueInt('DATA.Mysticism')));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Restoration', RecordValueInt('DATA.Restoration'));
    SetElementNativeValues(rec, 'DNAM\Skill Values\Enchanting', RecordValueInt('DATA.Intelligence') div 3);
  end;
  SetElementNativeValues(rec, 'DNAM\Health', health);
  SetElementNativeValues(rec, 'DNAM\Magicka', magicka);
  SetElementNativeValues(rec, 'DNAM\Stamina', stamina);
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportDIAL();
var
  rec: IInterface;
  tes4Type, tes5Type: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'DIAL');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  // TES4 dialog types: 0=Topic,1=Conversation,2=Combat,3=Persuasion,4=Detection,5=Service,6=Misc
  // TES5 dialog types: 0=Topic,1=Conversation,2=Combat,3=Persuasion,4=Detection,5=Service,6=Misc,7=CustomTopic,...
  tes4Type := RecordValueInt('DATA.Type');
  tes5Type := tes4Type; // Direct mapping for 0-6
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Category', tes5Type);
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportDOOR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'DOOR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'FNAM', True);
  SetElementNativeValues(rec, 'FNAM', RecordValueInt('FNAM.Flags'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportEFSH();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'EFSH');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // Fill texture
  if RecordValue('FillTexture') <> '' then begin
    Add(rec, 'ICON', True);
    SetElementEditValues(rec, 'ICON', RecordValue('FillTexture'));
  end;
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Flags', RecordValueInt('DATA.Flags'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportENCH();
var
  rec: IInterface;
  tes4Type, tes5Type: Integer;
  tes4Flags, tes5Flags: Integer;
  castType, targetType: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'ENCH');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  
  // ENIT - per Skyblivion mapping
  tes4Type := RecordValueInt('ENIT.Type');
  // Type mapping: Staff -> Staff Enchantment, else -> Enchantment
  if tes4Type = 1 then tes5Type := 12  // Staff Enchantment
  else tes5Type := 6;                   // Enchantment
  
  // Cast Type mapping per Skyblivion:
  // 0=Scroll -> Scroll(4), 1=Staff -> Fire and Forget(2),
  // 2=Weapon -> Fire and Forget(2), 3=Apparel -> Constant Effect(0)
  case tes4Type of
    0: castType := 4; // Scroll
    1: castType := 2; // Fire and Forget
    2: castType := 2; // Fire and Forget
    3: castType := 0; // Constant Effect
  else
    castType := 2;
  end;
  
  // Flag mapping: only No Auto Calc (bit 0)
  tes4Flags := RecordValueInt('ENIT.AutoCalc');
  tes5Flags := 0;
  if (tes4Flags and 1) <> 0 then tes5Flags := tes5Flags or 1;
  
  // Target type from first effect's EFIT\Type
  targetType := RecordValueInt('FirstEffect.Type');
  
  Add(rec, 'ENIT', True);
  SetElementNativeValues(rec, 'ENIT\Enchantment Cost', RecordValueInt('ENIT.Cost'));
  SetElementNativeValues(rec, 'ENIT\Flags', tes5Flags);
  SetElementNativeValues(rec, 'ENIT\Cast Type', castType);
  SetElementNativeValues(rec, 'ENIT\Enchantment Amount', RecordValueInt('ENIT.Charge'));
  SetElementNativeValues(rec, 'ENIT\Target Type', targetType);
  SetElementNativeValues(rec, 'ENIT\Enchant Type', tes5Type);
  SetElementNativeValues(rec, 'ENIT\Charge Time', 0.0);
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportEYES();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'EYES');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  if RecordValue('ICON') <> '' then begin
    Add(rec, 'ICON', True);
    SetElementEditValues(rec, 'ICON', RecordValue('ICON'));
  end;
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Flags', RecordValueInt('DATA.Playable'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportFACT();
var
  rec: IInterface;
  tes4Flags, tes5Flags: Integer;
  rankCount, relCount, i: Integer;
  prefix: string;
  modifier: Integer;
  reaction: Integer;
  relArray, relEntry: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'FACT');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  // DATA flags per Skyblivion mapping
  tes4Flags := RecordValueInt('DATA.Flags');
  tes5Flags := 0;
  // Hidden from PC (same bit)
  if (tes4Flags and $01) <> 0 then tes5Flags := tes5Flags or $01;
  // Evil flag -> ALL crime flags
  if (tes4Flags and $02) <> 0 then
    tes5Flags := tes5Flags or ($0080 + $0100 + $0200 + $0400 + $0800 + $2000 + $10000);
  // Special Combat (bit 2 -> bit 1)
  if (tes4Flags and $04) <> 0 then tes5Flags := tes5Flags or $02;
  // All factions can be owner per Skyblivion
  tes5Flags := tes5Flags or $8000;
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Flags', tes5Flags);
  
  // Crime gold (CRVA)
  Add(rec, 'CRVA', True);
  SetElementNativeValues(rec, 'CRVA\Murder',
    Round(RecordValueFloat('CNAM.CrimeGoldMult') * 1000));
  
  // Inter-faction Relations
  relCount := RecordValueInt('RelationCount');
  if relCount > 0 then begin
    Add(rec, 'Relations', True);
    relArray := ElementByName(rec, 'Relations');
    if Assigned(relArray) then begin
      for i := 0 to relCount - 1 do begin
        prefix := 'Rel[' + IntToStr(i) + ']';
        relEntry := ElementAssign(relArray, HighInteger, nil, False);
        if Assigned(relEntry) then begin
          // Faction FormID will be resolved in relink
          // Disposition -> Group Combat Reaction per Skyblivion
          modifier := RecordValueInt(prefix + '.Modifier');
          if modifier <= -50 then reaction := 3      // Enemy
          else if modifier = 100 then reaction := 1  // Ally
          else if modifier >= 50 then reaction := 2  // Friend
          else reaction := 0;                         // Neutral
          SetElementNativeValues(relEntry, 'Group Combat Reaction', reaction);
        end;
      end;
    end;
  end;
  
  // Ranks
  rankCount := RecordValueInt('RankCount');
  if rankCount > 0 then begin
    for i := 0 to rankCount - 1 do begin
      Add(rec, 'Ranks', True);
    end;
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportFLOR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'FLOR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportFURN();
var
  rec: IInterface;
  markerFlags: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'FURN');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  // MNAM - Active Markers (bitmask from TES4)
  markerFlags := RecordValueInt('MNAM.MarkerFlags');
  if markerFlags <> 0 then begin
    Add(rec, 'MNAM', True);
    SetElementNativeValues(rec, 'MNAM', markerFlags);
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportGLOB();
var
  rec: IInterface;
  typeStr: string;
begin
  rec := CreateNewRecord(TargetPlugin, 'GLOB');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  typeStr := RecordValue('FNAM.Type');
  Add(rec, 'FNAM', True);
  SetElementEditValues(rec, 'FNAM', typeStr);
  
  Add(rec, 'FLTV', True);
  SetElementNativeValues(rec, 'FLTV', RecordValueFloat('FLTV.Value'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportGMST();
var
  rec: IInterface;
  edid: string;
begin
  rec := CreateNewRecord(TargetPlugin, 'GMST');
  if not Assigned(rec) then Exit;
  
  edid := RecordValue('EditorID');
  SetEditorID(rec, edid);
  
  // GMST value type determined by first char of EditorID
  Add(rec, 'DATA', True);
  SetElementEditValues(rec, 'DATA', RecordValue('DATA.Value'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportGRAS();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'GRAS');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Density', RecordValueInt('DATA.Density'));
  SetElementNativeValues(rec, 'DATA\Min Slope', RecordValueInt('DATA.MinSlope'));
  SetElementNativeValues(rec, 'DATA\Max Slope', RecordValueInt('DATA.MaxSlope'));
  SetElementNativeValues(rec, 'DATA\Units From Water Amount', RecordValueInt('DATA.UnitFromWater'));
  SetElementNativeValues(rec, 'DATA\Units From Water Type', RecordValueInt('DATA.UnitFromWaterType'));
  SetElementNativeValues(rec, 'DATA\Position Range', RecordValueFloat('DATA.PosRange'));
  SetElementNativeValues(rec, 'DATA\Height Range', RecordValueFloat('DATA.HeightRange'));
  SetElementNativeValues(rec, 'DATA\Color Range', RecordValueFloat('DATA.ColorRange'));
  SetElementNativeValues(rec, 'DATA\Wave Period', RecordValueFloat('DATA.WavePeriod'));
  SetElementNativeValues(rec, 'DATA\Flags', RecordValueInt('DATA.Flags'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportHDPT();
// HAIR -> HDPT (Head Part)
var
  rec: IInterface;
  tes4Flags: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'HDPT');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  // TES5 HDPT DATA: Type enum (0=Misc,1=Face,2=Eyes,3=Hair,4=FacialHair,5=Scar,6=Eyebrows)
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Flags', 1); // Playable
  
  // PNAM (Type) = 3 (Hair)
  Add(rec, 'PNAM', True);
  SetElementNativeValues(rec, 'PNAM', 3);
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportIDLE();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'IDLE');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportINGR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'INGR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  // ENIT
  Add(rec, 'ENIT', True);
  SetElementNativeValues(rec, 'ENIT\Value', RecordValueInt('ENIT.Value'));
  
  // DATA Weight
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  // Effects need MGEF resolution
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportKEYM();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'KEYM');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportLIGH();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'LIGH');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Time', RecordValueInt('DATA.Time'));
  SetElementNativeValues(rec, 'DATA\Radius', RecordValueInt('DATA.Radius'));
  SetElementEditValues(rec, 'DATA\Color', RecordValue('DATA.Color'));
  SetElementNativeValues(rec, 'DATA\Flags', RecordValueInt('DATA.Flags'));
  SetElementNativeValues(rec, 'DATA\Falloff Exponent', RecordValueFloat('DATA.FalloffExp'));
  SetElementNativeValues(rec, 'DATA\FOV', RecordValueFloat('DATA.FOV'));
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  Add(rec, 'FNAM', True);
  SetElementNativeValues(rec, 'FNAM', RecordValueFloat('FNAM.Fade'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportLSCR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'LSCR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetDescription(rec, RecordValue('DESC'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportLTEX();
var
  rec, txstRec, arma: IInterface;
  iconPath, normalPath: string;
  matType: Integer;
  mattFormID: string;
begin
  rec := CreateNewRecord(TargetPlugin, 'LTEX');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // Create companion TXST record per Skyblivion/Skywind approach
  iconPath := RecordValue('ICON');
  if iconPath <> '' then begin
    txstRec := CreateNewRecord(TargetPlugin, 'TXST');
    if Assigned(txstRec) then begin
      SetEditorID(txstRec, RecordValue('EditorID') + 'Set');
      // TX00 - diffuse texture
      Add(txstRec, 'TX00', True);
      SetElementEditValues(txstRec, 'TX00', iconPath);
      // TX01 - normal map (derive by adding _n before extension)
      normalPath := iconPath;
      if Pos('.', normalPath) > 0 then
        normalPath := Copy(normalPath, 1, Length(normalPath) - 4) + '_n.dds';
      Add(txstRec, 'TX01', True);
      SetElementEditValues(txstRec, 'TX01', normalPath);
      FinishRecord(txstRec);
      
      // TNAM on LTEX -> points to TXST
      Add(rec, 'TNAM', True);
      SetElementEditValues(rec, 'TNAM', IntToHex(GetLoadOrderFormID(txstRec), 8));
    end;
  end;
  
  // HNAM - Material Type -> MATT FormID mapping per Skywind
  matType := RecordValueInt('HNAM.Material');
  case matType of
    0:  mattFormID := '00012F34'; // Stone
    1:  mattFormID := '00012F37'; // Cloth
    2:  mattFormID := '00012F38'; // Dirt
    3:  mattFormID := '00012F39'; // Glass
    4:  mattFormID := '00012F46'; // Grass
    5:  mattFormID := '00012F3C'; // Metal (Solid)
    6:  mattFormID := '00012F3D'; // Organic
    7:  mattFormID := '00012F3F'; // Skin
    8:  mattFormID := '00012F40'; // Water
    9:  mattFormID := '00012F41'; // Wood (Light)
    10: mattFormID := '00012F36'; // Heavy Stone
    11: mattFormID := '00012F3B'; // Heavy Metal
    12: mattFormID := '00012F42'; // Heavy Wood
    13: mattFormID := '00028E99'; // Chain
    14: mattFormID := '00012F45'; // Snow
  else
    mattFormID := '00012F46'; // default -> Grass
  end;
  Add(rec, 'MNAM', True);
  SetElementEditValues(rec, 'MNAM', mattFormID);
  
  Add(rec, 'HNAM', True);
  SetElementNativeValues(rec, 'HNAM\Friction', RecordValueInt('HNAM.Friction'));
  SetElementNativeValues(rec, 'HNAM\Restitution', RecordValueInt('HNAM.Restitution'));
  
  Add(rec, 'SNAM', True);
  SetElementNativeValues(rec, 'SNAM', RecordValueInt('SNAM.Specular'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportLVLN();
// LVLC -> LVLN (Leveled NPC)
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'LVLN');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  Add(rec, 'LVLD', True);
  SetElementNativeValues(rec, 'LVLD', RecordValueInt('LVLD.ChanceNone'));
  
  Add(rec, 'LVLF', True);
  SetElementNativeValues(rec, 'LVLF', RecordValueInt('LVLF.Flags'));
  
  // Entries need FormID resolution
  // TES5 uses LLCT for count
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportLVLI();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'LVLI');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  Add(rec, 'LVLD', True);
  SetElementNativeValues(rec, 'LVLD', RecordValueInt('LVLD.ChanceNone'));
  
  Add(rec, 'LVLF', True);
  SetElementNativeValues(rec, 'LVLF', RecordValueInt('LVLF.Flags'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportLVSP();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'LVSP');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  Add(rec, 'LVLD', True);
  SetElementNativeValues(rec, 'LVLD', RecordValueInt('LVLD.ChanceNone'));
  
  Add(rec, 'LVLF', True);
  SetElementNativeValues(rec, 'LVLF', RecordValueInt('LVLF.Flags'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportMGEF();
var
  rec: IInterface;
  tes4Flags, tes5Flags: Integer;
  school, tes5AV: Integer;
  resistVal: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'MGEF');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetDescription(rec, RecordValue('DESC'));
  
  // TES5 MGEF uses a completely different data structure
  // DATA subrecord in TES5 has: Flags, Base Cost, Assoc Item, Magic Skill,
  //   Resist Value, Counter Effect Count, etc.
  Add(rec, 'DATA', True);
  
  // Map TES4 flags to TES5
  tes4Flags := RecordValueInt('DATA.Flags');
  tes5Flags := 0;
  if (tes4Flags and 1) <> 0 then tes5Flags := tes5Flags or 1;       // Hostile
  if (tes4Flags and 2) <> 0 then tes5Flags := tes5Flags or 2;       // Recover
  if (tes4Flags and 4) <> 0 then tes5Flags := tes5Flags or 4;       // Detrimental
  if (tes4Flags and $800) <> 0 then tes5Flags := tes5Flags or $200;  // Spellmaking -> No Area
  
  SetElementNativeValues(rec, 'DATA\Flags', tes5Flags);
  SetElementNativeValues(rec, 'DATA\Base Cost', RecordValueFloat('DATA.BaseCost'));
  
  // Magic School mapping: TES4 0-5 -> TES5 Magic Skill ActorValue
  // TES4: 0=Alteration,1=Conjuration,2=Destruction,3=Illusion,4=Mysticism,5=Restoration
  // TES5 skills: 18=Alteration,19=Conjuration,20=Destruction,21=Illusion,22=Restoration
  school := RecordValueInt('DATA.MagicSchool');
  case school of
    0: tes5AV := 18; // Alteration
    1: tes5AV := 19; // Conjuration
    2: tes5AV := 20; // Destruction
    3: tes5AV := 21; // Illusion
    4: tes5AV := 21; // Mysticism -> Illusion
    5: tes5AV := 22; // Restoration
  else
    tes5AV := -1;
  end;
  SetElementNativeValues(rec, 'DATA\Magic Skill', tes5AV);
  
  // Resist value mapping
  resistVal := RecordValueInt('DATA.ResistValue');
  SetElementNativeValues(rec, 'DATA\Resist Value', MapTES4ActorValueToTES5(resistVal));
  
  // Casting Type: default to Fire and Forget (2)
  SetElementNativeValues(rec, 'DATA\Casting Type', 2);
  // Delivery: derive from TES4 flags (Self=0, Touch=1, Aimed=2)
  // tes4Flags already read above
  if (tes4Flags and $10) <> 0 then
    SetElementNativeValues(rec, 'DATA\Delivery', 0)  // Self
  else if (tes4Flags and $20) <> 0 then
    SetElementNativeValues(rec, 'DATA\Delivery', 1)  // Contact
  else if (tes4Flags and $40) <> 0 then
    SetElementNativeValues(rec, 'DATA\Delivery', 2)  // Aimed
  else
    SetElementNativeValues(rec, 'DATA\Delivery', 0);
  
  // Minimum Skill Level = 0
  SetElementNativeValues(rec, 'DATA\Minimum Skill Level', 0);
  
  // Description in DNAM for TES5
  if RecordValue('DESC') <> '' then begin
    Add(rec, 'DNAM', True);
    SetElementEditValues(rec, 'DNAM', RecordValue('DESC'));
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportMISC();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'MISC');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportPACK();
var
  rec: IInterface;
begin
  // TES5 Package system is completely different (procedural tree-based)
  // We create a basic skeleton
  rec := CreateNewRecord(TargetPlugin, 'PACK');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // TES5 PKDT is very different. Mark for manual review.
  Add(rec, 'PKDT', True);
  // Basic flags only
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportQUST();
var
  rec: IInterface;
  stageCount, logCount, targetCount: Integer;
  i, j: Integer;
  prefix: string;
begin
  rec := CreateNewRecord(TargetPlugin, 'QUST');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  // DNAM (Data) in TES5
  Add(rec, 'DNAM', True);
  // TES5 quest flags: 0=StartGameEnabled,1=Completed,2=AddIdleTopicToHello,...
  SetElementNativeValues(rec, 'DNAM\Flags', RecordValueInt('DATA.Flags'));
  SetElementNativeValues(rec, 'DNAM\Priority', RecordValueInt('DATA.Priority'));
  
  // Stages
  stageCount := RecordValueInt('StageCount');
  if stageCount > 0 then begin
    for i := 0 to stageCount - 1 do begin
      prefix := 'Stage[' + IntToStr(i) + ']';
      // TES5 quest stages are similar but have different sub-structures
      // Would need Add(rec, 'Stages', True) and populate
      // For now, log as needing manual fixup
    end;
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportRACE();
var
  rec: IInterface;
  boostCount, i, tes4Skill, tes5Skill: Integer;
  edid: string;
  skyrimFormID: Cardinal;
  overridden: Boolean;
begin
  edid := RecordValue('EditorID');
  overridden := False;
  skyrimFormID := 0;
  
  // Race override mapping: use Skyrim equivalent races where they exist
  // These FormIDs are from Skyrim.esm for the matching playable races
  if edid = 'Argonian' then skyrimFormID := $00013740
  else if edid = 'Breton' then skyrimFormID := $00013741
  else if edid = 'DarkElf' then skyrimFormID := $00013742
  else if edid = 'HighElf' then skyrimFormID := $00013743
  else if edid = 'Imperial' then skyrimFormID := $00013744
  else if edid = 'Khajiit' then skyrimFormID := $00013745
  else if edid = 'Nord' then skyrimFormID := $00013746
  else if edid = 'Orc' then skyrimFormID := $00013747
  else if edid = 'Redguard' then skyrimFormID := $00013748
  else if edid = 'WoodElf' then skyrimFormID := $00013749;
  
  if skyrimFormID <> 0 then begin
    // Find the Skyrim race record in loaded files
    rec := RecordByFormID(FileByIndex(0), skyrimFormID, True);
    if Assigned(rec) then begin
      overridden := True;
      AddMessage('  RACE ' + edid + ': overriding with Skyrim equivalent [' + IntToHex(skyrimFormID, 8) + ']');
      StoreFormIDMapping(RecordValue('FormID'), rec);
      Exit; // Don't create a new record, just map the FormID
    end;
  end;
  
  // Non-overridden races: create new record
  rec := CreateNewRecord(TargetPlugin, 'RACE');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, edid);
  SetFull(rec, RecordValue('FULL'));
  SetDescription(rec, RecordValue('DESC'));
  
  // DATA - TES5 RACE DATA
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Male Height', RecordValueFloat('DATA.MaleHeight'));
  SetElementNativeValues(rec, 'DATA\Female Height', RecordValueFloat('DATA.FemaleHeight'));
  SetElementNativeValues(rec, 'DATA\Male Weight', RecordValueFloat('DATA.MaleWeight'));
  SetElementNativeValues(rec, 'DATA\Female Weight', RecordValueFloat('DATA.FemaleWeight'));
  SetElementNativeValues(rec, 'DATA\Starting Health', 100);
  SetElementNativeValues(rec, 'DATA\Starting Magicka', 100);
  SetElementNativeValues(rec, 'DATA\Starting Stamina', 100);
  SetElementNativeValues(rec, 'DATA\Base Carry Weight', 300);
  SetElementNativeValues(rec, 'DATA\Health Regen', 0.7);
  SetElementNativeValues(rec, 'DATA\Magicka Regen', 3.0);
  SetElementNativeValues(rec, 'DATA\Stamina Regen', 5.0);
  SetElementNativeValues(rec, 'DATA\Unarmed Damage', 4.0);
  SetElementNativeValues(rec, 'DATA\Unarmed Reach', 75);
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportREFR();
var
  rec, parentCell: IInterface;
  parentCellID: string;
  lockLevel, mapType, tes5MapType: Integer;
begin
  parentCellID := RecordValue('ParentCELL');
  parentCell := FindMappedRecord(parentCellID);
  if Assigned(parentCell) then
    rec := CreateChildRecord(parentCell, 'REFR')
  else begin
    Inc(RefOrphanCount);
    if RefOrphanCount <= 5 then
      AddMessage('  WARNING: Orphan REFR ' + RecordValue('FormID') + ' - parent CELL ' + parentCellID + ' not found');
    Inc(SkippedCount);
    Exit;
  end;
  if not Assigned(rec) then begin Inc(SkippedCount); Exit; end;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // NAME (base object) - placeholder, relink phase resolves
  Add(rec, 'NAME', True);
  
  // Position/Rotation
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Position\X', RecordValueFloat('PosX'));
  SetElementNativeValues(rec, 'DATA\Position\Y', RecordValueFloat('PosY'));
  SetElementNativeValues(rec, 'DATA\Position\Z', RecordValueFloat('PosZ'));
  SetElementNativeValues(rec, 'DATA\Rotation\X', RecordValueFloat('RotX'));
  SetElementNativeValues(rec, 'DATA\Rotation\Y', RecordValueFloat('RotY'));
  SetElementNativeValues(rec, 'DATA\Rotation\Z', RecordValueFloat('RotZ'));
  
  // Scale
  if RecordHasKey('XSCL') then begin
    Add(rec, 'XSCL', True);
    SetElementNativeValues(rec, 'XSCL', RecordValueFloat('XSCL'));
  end;
  
  // Lock - convert lock level to Skyrim tiers per Skyblivion
  if RecordHasKey('XLOC.Level') then begin
    lockLevel := RecordValueInt('XLOC.Level');
    if lockLevel <> 255 then begin
      if lockLevel > 80 then lockLevel := 100      // Master
      else if lockLevel > 60 then lockLevel := 75  // Expert
      else if lockLevel > 40 then lockLevel := 50  // Adept
      else if lockLevel > 20 then lockLevel := 25  // Apprentice
      else lockLevel := 1;                          // Novice
    end;
    Add(rec, 'XLOC', True);
    SetElementNativeValues(rec, 'XLOC\Level', lockLevel);
    SetElementNativeValues(rec, 'XLOC\Flags', RecordValueInt('XLOC.Flags'));
  end;
  
  // Enable parent
  if RecordHasKey('XESP.Ref') then begin
    Add(rec, 'XESP', True);
    SetElementNativeValues(rec, 'XESP\Flags', RecordValueInt('XESP.Opposite'));
  end;
  
  // Count
  if RecordHasKey('XCNT') then begin
    Add(rec, 'XCNT', True);
    SetElementNativeValues(rec, 'XCNT', RecordValueInt('XCNT'));
  end;
  
  // Map Marker - with proper type mapping per Skyblivion
  if RecordHasKey('HasMapMarker') then begin
    Add(rec, 'Map Marker', True);
    Add(rec, 'XMRK', True);
    
    // Map marker flags (FNAM)
    SetElementNativeValues(rec, 'Map Marker\FNAM', RecordValueInt('MapMarker.Flags'));
    
    // Map marker name
    if RecordHasKey('MapMarker.Name') then begin
      Add(rec, 'Map Marker\FULL', True);
      SetElementEditValues(rec, 'Map Marker\FULL', UnescapeStr(RecordValue('MapMarker.Name')));
    end;
    
    // TNAM - Type mapping per Skyblivion
    mapType := RecordValueInt('MapMarker.Type');
    case mapType of
      1:  tes5MapType := 5;   // Camp -> Camp
      2:  tes5MapType := 4;   // Cave -> Cave
      3:  tes5MapType := 1;   // City -> City/Town
      4:  tes5MapType := 7;   // Ayleid Ruin -> Nordic Ruin
      5:  tes5MapType := 6;   // Fort Ruin -> Fort
      7:  tes5MapType := 11;  // Landmark -> Landmark
      8:  tes5MapType := 14;  // Tavern -> Inn
      9:  tes5MapType := 3;   // Settlement -> Settlement
      10: tes5MapType := 34;  // Daedric Shrine -> custom
      11: tes5MapType := 34;  // Oblivion Gate -> custom
      12: tes5MapType := 11;  // Door -> Landmark
    else
      tes5MapType := 11;      // default -> Landmark
    end;
    Add(rec, 'Map Marker\TNAM', True);
    SetElementNativeValues(rec, 'Map Marker\TNAM\Type', tes5MapType);
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportACHR();
var
  rec, parentCell: IInterface;
  parentCellID: string;
begin
  // ACHR must be created inside a parent CELL's child group
  parentCellID := RecordValue('ParentCELL');
  parentCell := FindMappedRecord(parentCellID);
  if Assigned(parentCell) then
    rec := CreateChildRecord(parentCell, 'ACHR')
  else begin
    Inc(RefOrphanCount);
    if RefOrphanCount <= 5 then
      AddMessage('  WARNING: Orphan ACHR ' + RecordValue('FormID') + ' - parent CELL ' + parentCellID + ' not found');
    Inc(SkippedCount);
    Exit;
  end;
  if not Assigned(rec) then begin Inc(SkippedCount); Exit; end;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // NAME base - placeholder, resolved in relink phase
  Add(rec, 'NAME', True);
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Position\X', RecordValueFloat('PosX'));
  SetElementNativeValues(rec, 'DATA\Position\Y', RecordValueFloat('PosY'));
  SetElementNativeValues(rec, 'DATA\Position\Z', RecordValueFloat('PosZ'));
  SetElementNativeValues(rec, 'DATA\Rotation\X', RecordValueFloat('RotX'));
  SetElementNativeValues(rec, 'DATA\Rotation\Y', RecordValueFloat('RotY'));
  SetElementNativeValues(rec, 'DATA\Rotation\Z', RecordValueFloat('RotZ'));
  
  if RecordHasKey('XSCL') then begin
    Add(rec, 'XSCL', True);
    SetElementNativeValues(rec, 'XSCL', RecordValueFloat('XSCL'));
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportREGN();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'REGN');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // RCLR map color
  if RecordHasKey('RCLR') then begin
    Add(rec, 'RCLR', True);
    SetElementEditValues(rec, 'RCLR', RecordValue('RCLR'));
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportSCRL();
// SGST -> SCRL (Sigil Stone -> Scroll)
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'SCRL');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportSLGM();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'SLGM');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  
  Add(rec, 'SOUL', True);
  SetElementNativeValues(rec, 'SOUL', RecordValueInt('SOUL'));
  
  Add(rec, 'SLCP', True);
  SetElementNativeValues(rec, 'SLCP', RecordValueInt('SLCP'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportSOUN();
var
  rec, sndrRec: IInterface;
  soundFile: string;
begin
  // TES5 splits SOUN into SOUN (marker) + SNDR (Sound Descriptor)
  // Create SOUN marker
  rec := CreateNewRecord(TargetPlugin, 'SOUN');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  
  // Create companion SNDR record with actual sound data per Skywind approach
  soundFile := RecordValue('FNAM.Filename');
  sndrRec := CreateNewRecord(TargetPlugin, 'SNDR');
  if Assigned(sndrRec) then begin
    SetEditorID(sndrRec, RecordValue('EditorID') + 'SD');
    
    // Set sound file path in SNDR
    if soundFile <> '' then begin
      Add(sndrRec, 'Sounds', True);
      // The ANAM path goes inside Sounds\Sound Files
      // Try setting the sound file directly
      SetElementEditValues(sndrRec, 'ANAM', soundFile);
    end;
    
    FinishRecord(sndrRec);
    
    // Link SOUN to SNDR via SDSC
    Add(rec, 'SDSC', True);
    SetElementEditValues(rec, 'SDSC', IntToHex(GetLoadOrderFormID(sndrRec), 8));
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportSPEL();
var
  rec: IInterface;
  tes4Type, tes5Type: Integer;
  tes4Flags, tes5Flags: Integer;
  targetType: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'SPEL');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetDescription(rec, RecordValue('DESC'));
  
  // SPIT - Flag remapping per Skyblivion
  tes4Flags := RecordValueInt('SPIT.Flags');
  tes5Flags := 0;
  if (tes4Flags and $01) <> 0 then tes5Flags := tes5Flags or $01;       // Manual Spell Cost
  if (tes4Flags and $10) <> 0 then tes5Flags := tes5Flags or $80000;    // Area Effect Ignores LOS
  if (tes4Flags and $20) <> 0 then tes5Flags := tes5Flags or $100000;   // Force Target (Ignores Resists)
  if (tes4Flags and $40) <> 0 then tes5Flags := tes5Flags or $200000;   // No Absorb/Reflect
  
  tes4Type := RecordValueInt('SPIT.Type');
  tes5Type := tes4Type; // Direct mapping for 0-5
  
  // Target type from first effect
  targetType := RecordValueInt('FirstEffect.Type');
  
  Add(rec, 'SPIT', True);
  SetElementNativeValues(rec, 'SPIT\Base Cost', RecordValueInt('SPIT.Cost'));
  SetElementNativeValues(rec, 'SPIT\Flags', tes5Flags);
  SetElementNativeValues(rec, 'SPIT\Type', tes5Type);
  SetElementNativeValues(rec, 'SPIT\Charge Time', 0.0);
  SetElementNativeValues(rec, 'SPIT\Cast Type', 2);     // Fire and Forget per Skyblivion
  SetElementNativeValues(rec, 'SPIT\Target Type', targetType);
  SetElementNativeValues(rec, 'SPIT\Cast Duration', 0.0);
  SetElementNativeValues(rec, 'SPIT\Range', 0.0);
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportSTAT();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'STAT');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetModel(rec, RecordValue('Model.MODL'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportTREE();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'TREE');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetModel(rec, RecordValue('SPTFile.MODL'));
  
  // TES5 TREE CNAM has different fields
  Add(rec, 'CNAM', True);
  SetElementNativeValues(rec, 'CNAM\Trunk Flexibility', RecordValueFloat('CNAM.RockSpeed'));
  SetElementNativeValues(rec, 'CNAM\Branch Flexibility', RecordValueFloat('CNAM.RustleSpeed'));
  SetElementNativeValues(rec, 'CNAM\Leaf Amplitude', RecordValueFloat('CNAM.LeafCurve'));
  SetElementNativeValues(rec, 'CNAM\Leaf Frequency', RecordValueFloat('CNAM.LeafDim'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportWATR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'WATR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  Add(rec, 'ANAM', True);
  SetElementNativeValues(rec, 'ANAM', RecordValueInt('ANAM.Opacity'));
  
  // TES5 WATR DATA/DNAM structure is very different
  // Basic properties that exist in both
  Add(rec, 'DNAM', True);
  SetElementNativeValues(rec, 'DNAM\Wind Velocity', RecordValueFloat('DATA.WindVelocity'));
  SetElementNativeValues(rec, 'DNAM\Wind Direction', RecordValueFloat('DATA.WindDirection'));
  SetElementNativeValues(rec, 'DNAM\Fresnel Amount', RecordValueFloat('DATA.Fresnel'));
  SetElementNativeValues(rec, 'DNAM\Damage', RecordValueInt('DATA.Damage'));
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportWEAP();
var
  rec: IInterface;
  animType: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'WEAP');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  AddOBND(rec);
  SetFull(rec, RecordValue('FULL'));
  SetModel(rec, RecordValue('Model.MODL'));
  
  // DNAM (weapon data in TES5)
  Add(rec, 'DNAM', True);
  animType := RecordValueInt('TES5.AnimType');
  SetElementNativeValues(rec, 'DNAM\Animation Type', animType);
  SetElementNativeValues(rec, 'DNAM\Speed', RecordValueFloat('DATA.Speed'));
  SetElementNativeValues(rec, 'DNAM\Reach', RecordValueFloat('DATA.Reach'));
  SetElementNativeValues(rec, 'DNAM\Stagger', 0.0);
  SetElementNativeValues(rec, 'DNAM\Base VATS To-Hit Chance', 0);
  SetElementNativeValues(rec, 'DNAM\Animation Attack Mult', 1.0);
  SetElementNativeValues(rec, 'DNAM\Range Min', 0.0);
  SetElementNativeValues(rec, 'DNAM\Range Max', 0.0);
  SetElementNativeValues(rec, 'DNAM\On Hit', 0);
  SetElementNativeValues(rec, 'DNAM\Rumble - Left Motor Strength', 0.5);
  SetElementNativeValues(rec, 'DNAM\Rumble - Right Motor Strength', 0.5);
  SetElementNativeValues(rec, 'DNAM\Rumble - Duration', 0.25);
  // Skill: map weapon type to skill
  // Bows=8 (Archery), everything else=6 (One-Handed) or 7 (Two-Handed)
  case animType of
    1, 2, 3, 4: SetElementNativeValues(rec, 'DNAM\Skill', 6); // One-Handed
    5, 6:       SetElementNativeValues(rec, 'DNAM\Skill', 7); // Two-Handed
    7:          SetElementNativeValues(rec, 'DNAM\Skill', 8); // Archery (Bow)
  else
    SetElementNativeValues(rec, 'DNAM\Skill', 6);
  end;
  
  // DATA (Value U32, Weight Float, Damage U16 in TES5)
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Value', RecordValueInt('DATA.Value'));
  SetElementNativeValues(rec, 'DATA\Weight', RecordValueFloat('DATA.Weight'));
  SetElementNativeValues(rec, 'DATA\Damage', RecordValueInt('DATA.Damage'));
  
  // Critical data
  Add(rec, 'CRDT', True);
  SetElementNativeValues(rec, 'CRDT\Damage', RecordValueInt('DATA.Damage') div 2);
  SetElementNativeValues(rec, 'CRDT\% Mult', 1.0);
  SetElementNativeValues(rec, 'CRDT\Flags', 0);
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportWTHR();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'WTHR');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  
  // Cloud textures
  // TES5 has different cloud layer system
  if RecordValue('CNAM.CloudLower') <> '' then begin
    // TES5 uses 00TX-3FTX for cloud textures
    // For simplicity, map lower layer cloud
  end;
  
  // DATA
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Wind Speed', RecordValueInt('DATA.WindSpeed'));
  SetElementNativeValues(rec, 'DATA\Trans Delta', RecordValueInt('DATA.TransDelta'));
  SetElementNativeValues(rec, 'DATA\Sun Glare', RecordValueInt('DATA.SunGlare'));
  SetElementNativeValues(rec, 'DATA\Sun Damage', RecordValueInt('DATA.SunDamage'));
  
  // HDR data
  if RecordHasKey('HNAM.EyeAdaptSpeed') then begin
    Add(rec, 'HNAM', True);
    SetElementNativeValues(rec, 'HNAM\Eye Adapt Speed', RecordValueFloat('HNAM.EyeAdaptSpeed'));
    SetElementNativeValues(rec, 'HNAM\Bloom Blur Radius', RecordValueFloat('HNAM.BlurRadius'));
    SetElementNativeValues(rec, 'HNAM\Bloom Threshold', RecordValueFloat('HNAM.EmissiveMult'));
    SetElementNativeValues(rec, 'HNAM\Bloom Scale', RecordValueFloat('HNAM.BrightScale'));
    SetElementNativeValues(rec, 'HNAM\Sunlight Dimmer', RecordValueFloat('HNAM.BrightClamp'));
  end;
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportLAND();
var
  rec, parentCell: IInterface;
  parentCellID: string;
  el, rows, row: IInterface;
  layers, layer, vtxtList, vtxtEntry: IInterface;
  i, j, k, layerCount, vtxtCount: Integer;
  key, prefix: string;
begin
  // LAND must be created inside a parent CELL's child group
  parentCellID := RecordValue('ParentCELL');
  parentCell := FindMappedRecord(parentCellID);
  if Assigned(parentCell) then
    rec := CreateChildRecord(parentCell, 'LAND')
  else begin
    Inc(RefOrphanCount);
    if RefOrphanCount <= 5 then
      AddMessage('  WARNING: Orphan LAND ' + RecordValue('FormID') + ' - parent CELL ' + parentCellID + ' not found');
    Inc(SkippedCount);
    Exit;
  end;
  if not Assigned(rec) then begin Inc(SkippedCount); Exit; end;

  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA', RecordValueInt('DATA.Flags'));

  // NOTE: Vertex data (VHGT, VNML, VCLR) is NOT imported here.
  // These are binary-identical between TES4/TES5 and are copied directly
  // by the land_copy.py post-processor for much better performance.

  // Layers - flat array of Base Layer (BTXT) and Alpha Layer (ATXT+VTXT)
  layerCount := RecordValueInt('LayerCount');
  if layerCount > 0 then begin
    layers := Add(rec, 'Layers', True);
    if Assigned(layers) then begin
      for i := 0 to layerCount - 1 do begin
        prefix := 'Layer[' + IntToStr(i) + ']';
        layer := ElementAssign(layers, HighInteger, nil, False);
        if not Assigned(layer) then Continue;

        if RecordValue(prefix + '.Type') = 'BASE' then begin
          // Base Layer - default ElementAssign creates BTXT, just set values
          Add(layer, 'BTXT', True);
          SetElementEditValues(layer, 'BTXT\Texture', RecordValue(prefix + '.BTXT.Texture'));
          SetElementNativeValues(layer, 'BTXT\Quadrant', RecordValueInt(prefix + '.BTXT.Quadrant'));
        end
        else if RecordValue(prefix + '.Type') = 'ALPHA' then begin
          // Alpha Layer - remove default BTXT, add ATXT + VTXT
          el := ElementBySignature(layer, 'BTXT');
          if Assigned(el) then
            RemoveElement(layer, el);
          Add(layer, 'ATXT', True);
          SetElementEditValues(layer, 'ATXT\Texture', RecordValue(prefix + '.ATXT.Texture'));
          SetElementNativeValues(layer, 'ATXT\Quadrant', RecordValueInt(prefix + '.ATXT.Quadrant'));
          SetElementNativeValues(layer, 'ATXT\Layer', RecordValueInt(prefix + '.ATXT.Layer'));

          // VTXT opacity data
          vtxtCount := RecordValueInt(prefix + '.VTXTCount');
          if vtxtCount > 0 then begin
            vtxtList := Add(layer, 'VTXT', True);
            if Assigned(vtxtList) then begin
              for k := 0 to vtxtCount - 1 do begin
                vtxtEntry := ElementAssign(vtxtList, HighInteger, nil, False);
                if Assigned(vtxtEntry) then begin
                  SetElementNativeValues(vtxtEntry, 'Position',
                    RecordValueInt(prefix + '.VT[' + IntToStr(k) + '].Pos'));
                  SetElementNativeValues(vtxtEntry, 'Opacity',
                    RecordValueFloat(prefix + '.VT[' + IntToStr(k) + '].Op'));
                end;
              end;
            end;
          end;
        end;
      end;
    end;
  end;

  // VTEX - texture FormID list (will need relinking)
  if RecordHasKey('VTEXCount') then begin
    el := Add(rec, 'VTEX', True);
    if Assigned(el) then begin
      for i := 0 to RecordValueInt('VTEXCount') - 1 do begin
        key := 'VTEX[' + IntToStr(i) + ']';
        if RecordHasKey(key) then begin
          row := ElementAssign(el, HighInteger, nil, False);
          if Assigned(row) then
            SetEditValue(row, RecordValue(key));
        end;
      end;
    end;
  end;

  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportCSTY();
var
  rec: IInterface;
begin
  rec := CreateNewRecord(TargetPlugin, 'CSTY');
  if not Assigned(rec) then Exit;

  SetEditorID(rec, RecordValue('EditorID'));

  // TES5 CSTY uses CSGD/CSMD/CSME instead of CSTD/CSAD
  // Map the basic combat style values that have equivalents
  Add(rec, 'CSGD', True);
  SetElementNativeValues(rec, 'CSGD\Offensive Mult', 0.5);
  SetElementNativeValues(rec, 'CSGD\Defensive Mult', 0.5);
  SetElementNativeValues(rec, 'CSGD\Group Offensive Mult', 1.0);

  Add(rec, 'CSME', True);
  SetElementNativeValues(rec, 'CSME\Attack Staggered Mult', 1.0);
  SetElementNativeValues(rec, 'CSME\Power Attack Staggered Mult', 1.0);

  // Map TES4 values where possible
  if RecordHasKey('CSTD.RangeMultOpt') then begin
    Add(rec, 'CSMD', True);
    SetElementNativeValues(rec, 'CSMD\Range Mult (Optimal)',
      RecordValueFloat('CSTD.RangeMultOpt'));
    SetElementNativeValues(rec, 'CSMD\Range Mult (Max)',
      RecordValueFloat('CSTD.RangeMultMax'));
  end;

  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportINFO();
var
  rec, parentDial, responses, resp: IInterface;
  parentDialID: string;
  respCount, i: Integer;
  prefix: string;
begin
  // INFO must be created inside a parent DIAL's child group
  parentDialID := RecordValue('ParentDIAL');
  parentDial := FindMappedRecord(parentDialID);
  if Assigned(parentDial) then
    rec := CreateChildRecord(parentDial, 'INFO')
  else begin
    rec := CreateNewRecord(TargetPlugin, 'INFO');
    if (parentDialID <> '') then
      AddMessage('  WARNING: Could not place INFO inside parent DIAL ' + parentDialID + ' - not found in mapping');
  end;
  if not Assigned(rec) then begin Inc(SkippedCount); Exit; end;

  // TES5 INFO response data
  respCount := RecordValueInt('ResponseCount');
  if respCount > 0 then begin
    // Create the Responses array container
    Add(rec, 'Responses', True);
    responses := ElementByName(rec, 'Responses');
    if Assigned(responses) then begin
      for i := 0 to respCount - 1 do begin
        prefix := 'Resp[' + IntToStr(i) + ']';
        // Add a new response entry to the array
        resp := ElementAssign(responses, HighInteger, nil, False);
        if Assigned(resp) then begin
          // TRDT - Response Data struct
          SetElementNativeValues(resp, 'TRDT\Emotion Type', RecordValueInt(prefix + '.EmotionType'));
          SetElementNativeValues(resp, 'TRDT\Emotion Value', RecordValueInt(prefix + '.EmotionValue'));
          SetElementNativeValues(resp, 'TRDT\Response number', RecordValueInt(prefix + '.ResponseNum'));
          // NAM1 - Response Text
          if RecordHasKey(prefix + '.Text') then
            SetElementEditValues(resp, 'NAM1', UnescapeStr(RecordValue(prefix + '.Text')));
          // NAM2 - Script Notes
          if RecordHasKey(prefix + '.Notes') then
            SetElementEditValues(resp, 'NAM2', UnescapeStr(RecordValue(prefix + '.Notes')));
        end;
      end;
    end;
  end;

  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

procedure ImportWRLD();
var
  rec: IInterface;
  flags: Integer;
begin
  rec := CreateNewRecord(TargetPlugin, 'WRLD');
  if not Assigned(rec) then Exit;
  
  SetEditorID(rec, RecordValue('EditorID'));
  SetFull(rec, RecordValue('FULL'));
  
  // DATA flags - clean per Skyblivion
  flags := RecordValueInt('DATA.Flags');
  // Clear Oblivion worldspace flag (bit 2)
  flags := flags and $FFFB;
  // Move 'No LOD Water' from bit 4 to bit 3
  if (flags and $10) <> 0 then begin
    flags := flags and $FFEF;  // clear bit 4
    flags := flags or $08;     // set bit 3
  end;
  
  Add(rec, 'DATA', True);
  SetElementNativeValues(rec, 'DATA\Flags', flags);
  
  // DNAM - Land Data (per Skyblivion: default heights)
  Add(rec, 'DNAM', True);
  SetElementNativeValues(rec, 'DNAM\Default Land Height', -2048.0);
  SetElementNativeValues(rec, 'DNAM\Default Water Height', 0.0);
  
  // NAMA - Distant LOD Multiplier
  Add(rec, 'NAMA', True);
  SetElementNativeValues(rec, 'NAMA', 1.0);
  
  // ONAM - Map Scale
  // Note: ONAM is a map data struct, may not exist as simple value
  
  // Parent worldspace, climate, water, music need FormID resolution in relink
  
  StoreFormIDMapping(RecordValue('FormID'), rec);
  FinishRecord(rec);
end;

//============================================================================
// Main Import Dispatcher
//============================================================================

procedure ProcessImportRecord();
var
  targetType, note: string;
begin
  targetType := RecordValue('TargetType');
  
  if targetType = '' then Exit;
  
  // Skip reference/info types
  if targetType = 'PGRD_SKIP' then begin Inc(SkippedCount); Exit; end;
  if targetType = 'ROAD_SKIP' then begin Inc(SkippedCount); Exit; end;
  if targetType = 'SCPT_SOURCE' then begin Inc(SkippedCount); Exit; end;
  if targetType = 'SKIL_REF' then begin Inc(SkippedCount); Exit; end;
  if targetType = 'BSGN_SPELLS' then begin Inc(SkippedCount); Exit; end;
  if targetType = 'UNKNOWN' then begin Inc(SkippedCount); Exit; end;

  // Skip types specified in settings
  if AutoSkipTypes <> '' then begin
    if Pos(',' + targetType + ',', AutoSkipTypes) > 0 then begin
      Inc(SkippedCount);
      Exit;
    end;
  end;
  
  // Dispatch to appropriate importer
  if targetType = 'ACTI' then ImportACTI()
  else if targetType = 'ALCH' then ImportALCH()
  else if targetType = 'AMMO' then ImportAMMO()
  else if targetType = 'ARMO' then ImportARMO()
  else if targetType = 'BOOK' then ImportBOOK()
  else if targetType = 'CELL' then ImportCELL()
  else if targetType = 'CLAS' then ImportCLAS()
  else if targetType = 'CLMT' then ImportCLMT()
  else if targetType = 'CONT' then ImportCONT()
  else if targetType = 'NPC_' then ImportNPC()
  else if targetType = 'CSTY' then ImportCSTY()
  else if targetType = 'DIAL' then ImportDIAL()
  else if targetType = 'DOOR' then ImportDOOR()
  else if targetType = 'EFSH' then ImportEFSH()
  else if targetType = 'ENCH' then ImportENCH()
  else if targetType = 'EYES' then ImportEYES()
  else if targetType = 'FACT' then ImportFACT()
  else if targetType = 'FLOR' then ImportFLOR()
  else if targetType = 'FURN' then ImportFURN()
  else if targetType = 'GLOB' then ImportGLOB()
  else if targetType = 'GMST' then ImportGMST()
  else if targetType = 'GRAS' then ImportGRAS()
  else if targetType = 'HDPT' then ImportHDPT()
  else if targetType = 'IDLE' then ImportIDLE()
  else if targetType = 'INFO' then ImportINFO()
  else if targetType = 'INGR' then ImportINGR()
  else if targetType = 'KEYM' then ImportKEYM()
  else if targetType = 'LAND' then ImportLAND()
  else if targetType = 'LIGH' then ImportLIGH()
  else if targetType = 'LSCR' then ImportLSCR()
  else if targetType = 'LTEX' then ImportLTEX()
  else if targetType = 'LVLN' then ImportLVLN()
  else if targetType = 'LVLI' then ImportLVLI()
  else if targetType = 'LVSP' then ImportLVSP()
  else if targetType = 'MGEF' then ImportMGEF()
  else if targetType = 'MISC' then ImportMISC()
  else if targetType = 'PACK' then ImportPACK()
  else if targetType = 'QUST' then ImportQUST()
  else if targetType = 'RACE' then ImportRACE()
  else if targetType = 'REFR' then ImportREFR()
  else if targetType = 'ACHR' then ImportACHR()
  else if targetType = 'REGN' then ImportREGN()
  else if targetType = 'SCRL' then ImportSCRL()
  else if targetType = 'SLGM' then ImportSLGM()
  else if targetType = 'SOUN' then ImportSOUN()
  else if targetType = 'SPEL' then ImportSPEL()
  else if targetType = 'STAT' then ImportSTAT()
  else if targetType = 'TREE' then ImportTREE()
  else if targetType = 'WATR' then ImportWATR()
  else if targetType = 'WEAP' then ImportWEAP()
  else if targetType = 'WTHR' then ImportWTHR()
  else if targetType = 'WRLD' then ImportWRLD()
  else if targetType = 'ANIO' then ImportSTAT() // ANIO skeletal - keep as static
  else begin
    AddMessage('  Unknown target type: ' + targetType + ' (EditorID: ' + RecordValue('EditorID') + ')');
    Inc(SkippedCount);
    Exit;
  end;
  
  Inc(RecordCount);
  if RecordCount mod 5000 = 0 then
    AddMessage('  Imported ' + IntToStr(RecordCount) + ' records (skipped: ' + IntToStr(SkippedCount) + ', orphans: ' + IntToStr(RefOrphanCount) + ')...');
end;

//============================================================================
// Entry Points
//============================================================================

function Initialize: Integer;
var
  importFile, settingsFile, mappingFile: string;
  i: Integer;
  ErrorCount: Integer;
  slSettings, slMasterMap: TStringList;
  masterParts: TStringList;
  masterFile: string;
begin
  Result := 0;
  AutoImportFile := '';
  AutoOutputName := '';
  AutoMappingDir := '';
  AutoMasterMappings := '';
  AutoExportDir := '';
  AutoSkipTypes := '';
  ImportPath := DataPath + 'TES4Export\';
  importFile := ImportPath + 'TES4_Records.txt';
  
  // Check for automation settings file
  settingsFile := ScriptsPath + 'conversion_settings.txt';
  if FileExists(settingsFile) then begin
    slSettings := TStringList.Create;
    try
      slSettings.LoadFromFile(settingsFile);
      if slSettings.Values['MODE'] = 'IMPORT' then begin
        if slSettings.Values['IMPORT_FILE'] <> '' then begin
          AutoImportFile := slSettings.Values['IMPORT_FILE'];
          importFile := AutoImportFile;
        end;
        if slSettings.Values['OUTPUT_NAME'] <> '' then
          AutoOutputName := slSettings.Values['OUTPUT_NAME'];
        if slSettings.Values['MAPPING_DIR'] <> '' then
          AutoMappingDir := IncludeTrailingBackslash(slSettings.Values['MAPPING_DIR']);
        if slSettings.Values['MASTER_MAPPINGS'] <> '' then
          AutoMasterMappings := slSettings.Values['MASTER_MAPPINGS'];
        if slSettings.Values['EXPORT_DIR'] <> '' then begin
          AutoExportDir := IncludeTrailingBackslash(slSettings.Values['EXPORT_DIR']);
          ImportPath := AutoExportDir;
        end;
        if slSettings.Values['SKIP_TYPES'] <> '' then
          AutoSkipTypes := ',' + slSettings.Values['SKIP_TYPES'] + ',';
      end;
    finally
      slSettings.Free;
    end;
  end;
  
  if not FileExists(importFile) then begin
    AddMessage('ERROR: Import file not found: ' + importFile);
    AddMessage('Run TES4_Export_Records.pas in TES4Edit first.');
    Result := 1;
    Exit;
  end;
  
  // Get target plugin
  TargetPlugin := nil;
  if AutoOutputName <> '' then begin
    // In automation mode, find the plugin matching OutputName among loaded files
    for i := 0 to FileCount - 1 do begin
      if SameText(GetFileName(FileByIndex(i)), AutoOutputName) then begin
        TargetPlugin := FileByIndex(i);
        Break;
      end;
    end;
    // If not loaded, create a new plugin
    if not Assigned(TargetPlugin) then begin
      if not FileExists(DataPath + AutoOutputName) then begin
        AddMessage('Creating new plugin: ' + AutoOutputName);
        TargetPlugin := AddNewFileName(AutoOutputName, False);
        if Assigned(TargetPlugin) then begin
          // Set ESM flag if source was .esm
          if SameText(ExtractFileExt(AutoOutputName), '.esm') then
            SetIsESM(TargetPlugin, True);
        end;
      end else
        AddMessage('WARNING: ' + AutoOutputName + ' exists in data path but was not loaded. Select it in the module dialog.');
    end;
  end;
  // Fallback: pick the first writable non-ESM (manual mode)
  if not Assigned(TargetPlugin) then begin
    for i := 0 to FileCount - 1 do begin
      if not GetIsESM(FileByIndex(i)) then begin
        if Pos('.exe', LowerCase(GetFileName(FileByIndex(i)))) = 0 then begin
          TargetPlugin := FileByIndex(i);
          Break;
        end;
      end;
    end;
  end;
  
  if not Assigned(TargetPlugin) then begin
    AddMessage('ERROR: No target .esp plugin found. Create a new plugin first.');
    Result := 1;
    Exit;
  end;
  
  AddMessage('TES5 Record Import: Starting...');
  AddMessage('Import file: ' + importFile);
  AddMessage('Target plugin: ' + GetFileName(TargetPlugin));
  if AutoSkipTypes <> '' then
    AddMessage('Skipping types: ' + AutoSkipTypes);
  
  // Set TES5/SSE file header version
  SetElementEditValues(ElementByIndex(TargetPlugin, 0), 'HEDR\Version', '1.7100');
  SetFormVersion(ElementByIndex(TargetPlugin, 0), 44);
  
  // Add Skyrim.esm as a master so hardcoded FormIDs resolve correctly
  AddMasterIfMissing(TargetPlugin, 'Skyrim.esm');
  
  slImport := TStringList.Create;
  slImport.LoadFromFile(importFile);
  
  slFormIDMap := TStringList.Create;
  
  // Load master FormID mappings for dependency resolution
  if AutoMasterMappings <> '' then begin
    masterParts := TStringList.Create;
    try
      masterParts.Delimiter := ';';
      masterParts.StrictDelimiter := True;
      masterParts.DelimitedText := AutoMasterMappings;
      for i := 0 to masterParts.Count - 1 do begin
        masterFile := masterParts[i];
        if (masterFile <> '') and FileExists(masterFile) then begin
          slMasterMap := TStringList.Create;
          try
            slMasterMap.LoadFromFile(masterFile);
            AddMessage('Loaded master mapping: ' + masterFile + ' (' + IntToStr(slMasterMap.Count) + ' entries)');
            // Merge into our FormID map (master entries available for lookups)
            // These are pre-loaded so references to master records can resolve
            slFormIDMap.AddStrings(slMasterMap);
          finally
            slMasterMap.Free;
          end;
        end;
      end;
    finally
      masterParts.Free;
    end;
  end;
  
  recData := TStringList.Create;
  
  RecordCount := 0;
  SkippedCount := 0;
  CellFailCount := 0;
  RefOrphanCount := 0;
  CurrentLine := 0;
  ErrorCount := 0;
  
  AddMessage('Processing ' + IntToStr(slImport.Count) + ' lines from export file...');
  
  while ReadNextRecord() do begin
    try
      ProcessImportRecord();
    except
      Inc(ErrorCount);
      if ErrorCount <= 20 then
        AddMessage('  ERROR importing ' + RecordValue('TargetType') + ' ' + RecordValue('FormID') + ' (' + RecordValue('EditorID') + ') at line ' + IntToStr(CurrentLine))
      else if ErrorCount = 21 then
        AddMessage('  (suppressing further error messages - check summary at end)');
    end;
  end;
  recData.Free;
  
  // Save FormID mapping for cross-reference fixup and downstream dependents
  if AutoMappingDir <> '' then begin
    ForceDirectories(AutoMappingDir);
    mappingFile := AutoMappingDir + AutoOutputName + '.FormID_Mapping.txt';
  end else
    mappingFile := ImportPath + 'FormID_Mapping.txt';
  
  slFormIDMap.SaveToFile(mappingFile);
  
  AddMessage('');
  AddMessage('TES5 Record Import: Complete!');
  AddMessage('Records imported: ' + IntToStr(RecordCount));
  AddMessage('Records skipped: ' + IntToStr(SkippedCount));
  if ErrorCount > 0 then
    AddMessage('Records failed with errors: ' + IntToStr(ErrorCount));
  if CellFailCount > 0 then
    AddMessage('Cells failed to create: ' + IntToStr(CellFailCount));
  if RefOrphanCount > 0 then
    AddMessage('Orphaned child records (REFR/ACHR/LAND without parent CELL): ' + IntToStr(RefOrphanCount));
  AddMessage('FormID mappings: ' + IntToStr(slFormIDMap.Count));
  AddMessage('FormID mapping saved to: ' + mappingFile);
  AddMessage('');
  AddMessage('=== POST-IMPORT TASKS ===');
  AddMessage('1. Run TES5_Relink_References.pas to resolve cross-references');
  AddMessage('2. NIF meshes must be converted from Oblivion to Skyrim format');
  AddMessage('3. DDS textures may need recompression (DXT1/DXT5 -> BC formats for SSE)');
  AddMessage('4. TES4 scripts (SCPT) must be rewritten in Papyrus');
  
  slImport.Free;
  slFormIDMap.Free;
end;

function Process(e: IInterface): Integer;
begin
  // Not used - all processing done in Initialize
  Result := 0;
end;

function Finalize: Integer;
begin
  Result := 0;
end;

end.
