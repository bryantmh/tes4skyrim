{
  TES5_Relink_References.pas
  ==========================
  xEdit Pascal Script for TES V (Skyrim) - Cross-Reference Relinking

  Run AFTER TES5_Import_Records.pas has created all records.
  This script re-reads TES4_Records.txt and FormID_Mapping.txt to resolve
  all cross-references (FormID links) between imported records.

  Usage:
    1. Run TES5_Import_Records.pas first to create all records
    2. Select the import target plugin in TES5Edit
    3. Apply this script
    4. It reads FormID_Mapping.txt and TES4_Records.txt from <DataPath>\TES4Export\

  What it relinks:
    - REFR/ACHR: base object (NAME), teleport door, enable parent, lock key
    - NPC_: race, class, hair, combat style, death item, factions, spells,
            items, AI packages
    - CONT: items
    - LVLN/LVLI/LVSP: leveled list entries
    - ENCH: (skeleton only - effects need manual MGEF resolution)
    - SPEL: (skeleton only)
    - FACT: inter-faction relations
    - FLOR: ingredient
    - IDLE: parent, previous
    - LIGH: sound
    - MGEF: light, effect shader, sounds
    - CLMT: weather entries
    - RACE: spells
    - QUST: target refs
    - REGN: worldspace
    - WRLD: parent, climate, water
    - INFO: quest, topic, previous info, add topics

  What needs manual relinking:
    - Magic effects on ALCH/ENCH/INGR/SPEL/SGST (require MGEF FormID mapping)
    - PACK (TES5 package system is completely different)
    - LSCR locations
    - CSTY (combat style structure differs)
}
unit TES5_Relink_References;

var
  slMapping: TStringList;   // old FormID -> new FormID
  slImport: TStringList;    // raw export data
  recData: TStringList;     // current record data (global for JvInterpreter compat)
  TargetPlugin: IInterface;
  ImportPath: string;
  CurrentLine: Integer;
  LinkedCount: Integer;
  FailedCount: Integer;
  AutoOutputName: string;

//============================================================================
// Utility Functions
//============================================================================

function GetKey(const line: string): string;
var
  p: Integer;
begin
  p := Pos('=', line);
  if p > 0 then
    Result := Copy(line, 1, p - 1)
  else
    Result := line;
end;

function GetValue(const line: string): string;
var
  p: Integer;
begin
  p := Pos('=', line);
  if p > 0 then
    Result := Copy(line, p + 1, Length(line) - p)
  else
    Result := '';
end;

function ReadNextRecord: Boolean;
var
  line: string;
begin
  Result := False;
  recData.Clear;
  while CurrentLine < slImport.Count do begin
    line := slImport.Strings[CurrentLine];
    Inc(CurrentLine);
    if line = '---RECORD_BEGIN---' then begin
      Result := True;
      Break;
    end;
  end;
  if not Result then Exit;
  while CurrentLine < slImport.Count do begin
    line := slImport.Strings[CurrentLine];
    Inc(CurrentLine);
    if line = '---RECORD_END---' then
      Break;
    if (line <> '') and (Copy(line, 1, 1) <> '#') then
      recData.Add(line);
  end;
end;

function RecordValue(const key: string): string;
begin
  Result := recData.Values[key];
end;

function RecordValueInt(const key: string): Integer;
var
  s: string;
begin
  s := RecordValue(key);
  if s <> '' then
    Result := StrToIntDef(s, 0)
  else
    Result := 0;
end;

function RecordHasKey(const key: string): Boolean;
begin
  Result := (recData.IndexOfName(key) >= 0);
end;

// Collect all values matching a key prefix pattern like 'Item[0].FormID', 'Item[1].FormID', etc.
function CollectIndexedValues(const prefix, suffix: string): TStringList;
var
  i: Integer;
  k: string;
begin
  Result := TStringList.Create;
  for i := 0 to recData.Count - 1 do begin
    k := GetKey(recData.Strings[i]);
    if (Pos(prefix, k) = 1) and (Pos(suffix, k) > 0) then
      Result.Add(GetValue(recData.Strings[i]));
  end;
end;

//============================================================================
// FormID Resolution
//============================================================================

function ResolveFormID(const oldFormID: string): string;
// Returns the new TES5 FormID hex string for a given old TES4 FormID
begin
  Result := '';
  if (oldFormID = '') or (oldFormID = '00000000') then Exit;
  Result := slMapping.Values[oldFormID];
end;

function FindNewRecord(const oldFormID: string): IInterface;
// Find the new TES5 record that corresponds to an old TES4 FormID
var
  newFormIDStr: string;
  newFormID: Cardinal;
  i: Integer;
begin
  Result := nil;
  newFormIDStr := ResolveFormID(oldFormID);
  if newFormIDStr = '' then Exit;
  newFormID := StrToInt64('$' + newFormIDStr);
  // Search across all loaded files (target + masters)
  for i := 0 to FileCount - 1 do begin
    Result := RecordByFormID(FileByIndex(i), newFormID, True);
    if Assigned(Result) then Exit;
  end;
end;

procedure SetRef(rec: IInterface; const elemPath, oldFormID: string);
// Set a reference element on rec to point to the new record mapped from oldFormID
var
  newFormIDStr: string;
  newFormID: Cardinal;
  newRec: IInterface;
  i: Integer;
begin
  if (oldFormID = '') or (oldFormID = '00000000') then Exit;
  newFormIDStr := ResolveFormID(oldFormID);
  if newFormIDStr = '' then begin
    Inc(FailedCount);
    Exit;
  end;
  newFormID := StrToInt64('$' + newFormIDStr);
  // Search across all loaded files (target + masters)
  newRec := nil;
  for i := 0 to FileCount - 1 do begin
    newRec := RecordByFormID(FileByIndex(i), newFormID, True);
    if Assigned(newRec) then Break;
  end;
  if not Assigned(newRec) then begin
    Inc(FailedCount);
    Exit;
  end;
  try
    if not ElementExists(rec, elemPath) then
      Add(rec, elemPath, True);
    SetElementNativeValues(rec, elemPath, newFormID);
    Inc(LinkedCount);
  except
    Inc(FailedCount);
  end;
end;

//============================================================================
// Array Reference Relinking
//============================================================================

procedure RelinkItems(rec: IInterface);
var
  count, i: Integer;
  oldRef, newFormIDStr: string;
  newFormID: Cardinal;
  items, item, cnto: IInterface;
begin
  count := RecordValueInt('ItemCount');
  if count <= 0 then Exit;
  
  items := ElementByName(rec, 'Items');
  if not Assigned(items) then
    items := Add(rec, 'Items', True);
  
  for i := 0 to count - 1 do begin
    oldRef := RecordValue('Item[' + IntToStr(i) + '].FormID');
    newFormIDStr := ResolveFormID(oldRef);
    if newFormIDStr = '' then begin
      Inc(FailedCount);
      Continue;
    end;
    newFormID := StrToInt64('$' + newFormIDStr);
    
    // Add new item entry
    item := ElementAssign(items, HighInteger, nil, False);
    if Assigned(item) then begin
      SetElementNativeValues(item, 'CNTO\Item', newFormID);
      SetElementNativeValues(item, 'CNTO\Count',
        RecordValueInt('Item[' + IntToStr(i) + '].Count'));
      Inc(LinkedCount);
    end else
      Inc(FailedCount);
  end;
end;

procedure RelinkSpells(rec: IInterface);
var
  count, i: Integer;
  oldRef, newFormIDStr: string;
  newFormID: Cardinal;
  spells, spell: IInterface;
begin
  count := RecordValueInt('SpellCount');
  if count <= 0 then Exit;
  
  spells := ElementByName(rec, 'Spells');
  if not Assigned(spells) then
    spells := Add(rec, 'Spells', True);
  
  for i := 0 to count - 1 do begin
    oldRef := RecordValue('Spell[' + IntToStr(i) + ']');
    newFormIDStr := ResolveFormID(oldRef);
    if newFormIDStr = '' then begin
      Inc(FailedCount);
      Continue;
    end;
    newFormID := StrToInt64('$' + newFormIDStr);
    
    spell := ElementAssign(spells, HighInteger, nil, False);
    if Assigned(spell) then begin
      SetEditValue(spell, IntToHex(newFormID, 8));
      Inc(LinkedCount);
    end else
      Inc(FailedCount);
  end;
end;

procedure RelinkFactions(rec: IInterface);
var
  count, i: Integer;
  oldRef, newFormIDStr: string;
  newFormID: Cardinal;
  factions, faction: IInterface;
begin
  count := RecordValueInt('FactionCount');
  if count <= 0 then Exit;
  
  factions := ElementByName(rec, 'Factions');
  if not Assigned(factions) then
    factions := Add(rec, 'Factions', True);
  
  for i := 0 to count - 1 do begin
    oldRef := RecordValue('Faction[' + IntToStr(i) + '].FormID');
    newFormIDStr := ResolveFormID(oldRef);
    if newFormIDStr = '' then begin
      Inc(FailedCount);
      Continue;
    end;
    newFormID := StrToInt64('$' + newFormIDStr);
    
    faction := ElementAssign(factions, HighInteger, nil, False);
    if Assigned(faction) then begin
      SetElementNativeValues(faction, 'Faction', newFormID);
      SetElementNativeValues(faction, 'Rank',
        RecordValueInt('Faction[' + IntToStr(i) + '].Rank'));
      Inc(LinkedCount);
    end else
      Inc(FailedCount);
  end;
end;

procedure RelinkAIPackages(rec: IInterface);
var
  count, i: Integer;
  oldRef, newFormIDStr: string;
  newFormID: Cardinal;
  packages, pkg: IInterface;
begin
  count := RecordValueInt('AIPackageCount');
  if count <= 0 then Exit;
  
  packages := ElementByName(rec, 'AI Packages');
  if not Assigned(packages) then
    packages := Add(rec, 'AI Packages', True);
  
  for i := 0 to count - 1 do begin
    oldRef := RecordValue('AIPackage[' + IntToStr(i) + ']');
    newFormIDStr := ResolveFormID(oldRef);
    if newFormIDStr = '' then begin
      Inc(FailedCount);
      Continue;
    end;
    newFormID := StrToInt64('$' + newFormIDStr);
    
    pkg := ElementAssign(packages, HighInteger, nil, False);
    if Assigned(pkg) then begin
      SetEditValue(pkg, IntToHex(newFormID, 8));
      Inc(LinkedCount);
    end else
      Inc(FailedCount);
  end;
end;

procedure RelinkLeveledEntries(rec: IInterface);
var
  count, i: Integer;
  oldRef, newFormIDStr: string;
  newFormID: Cardinal;
  entries, entry: IInterface;
begin
  count := RecordValueInt('EntryCount');
  if count <= 0 then Exit;
  
  entries := ElementByName(rec, 'Leveled List Entries');
  if not Assigned(entries) then
    entries := Add(rec, 'Leveled List Entries', True);
  
  for i := 0 to count - 1 do begin
    oldRef := RecordValue('Entry[' + IntToStr(i) + '].FormID');
    newFormIDStr := ResolveFormID(oldRef);
    if newFormIDStr = '' then begin
      Inc(FailedCount);
      Continue;
    end;
    newFormID := StrToInt64('$' + newFormIDStr);
    
    entry := ElementAssign(entries, HighInteger, nil, False);
    if Assigned(entry) then begin
      SetElementNativeValues(entry, 'LVLO\Reference', newFormID);
      SetElementNativeValues(entry, 'LVLO\Level',
        RecordValueInt('Entry[' + IntToStr(i) + '].Level'));
      SetElementNativeValues(entry, 'LVLO\Count',
        RecordValueInt('Entry[' + IntToStr(i) + '].Count'));
      Inc(LinkedCount);
    end else
      Inc(FailedCount);
  end;
end;

procedure RelinkFactRelations(rec: IInterface);
var
  count, i: Integer;
  oldRef, newFormIDStr: string;
  newFormID: Cardinal;
  relations, rel: IInterface;
begin
  count := RecordValueInt('RelationCount');
  if count <= 0 then Exit;
  
  relations := ElementByName(rec, 'Relations');
  if not Assigned(relations) then
    relations := Add(rec, 'Relations', True);
  
  for i := 0 to count - 1 do begin
    oldRef := RecordValue('Rel[' + IntToStr(i) + '].Faction');
    newFormIDStr := ResolveFormID(oldRef);
    if newFormIDStr = '' then begin
      Inc(FailedCount);
      Continue;
    end;
    newFormID := StrToInt64('$' + newFormIDStr);
    
    rel := ElementAssign(relations, HighInteger, nil, False);
    if Assigned(rel) then begin
      SetElementNativeValues(rel, 'Faction', newFormID);
      SetElementNativeValues(rel, 'Modifier',
        RecordValueInt('Rel[' + IntToStr(i) + '].Modifier'));
      Inc(LinkedCount);
    end else
      Inc(FailedCount);
  end;
end;

procedure RelinkClimateWeathers(rec: IInterface);
var
  count, i: Integer;
  oldRef, newFormIDStr: string;
  newFormID: Cardinal;
  weathers, w: IInterface;
begin
  count := RecordValueInt('WeatherCount');
  if count <= 0 then Exit;
  
  weathers := ElementBySignature(rec, 'WLST');
  if not Assigned(weathers) then
    weathers := Add(rec, 'WLST', True);
  
  for i := 0 to count - 1 do begin
    oldRef := RecordValue('Weather[' + IntToStr(i) + '].FormID');
    newFormIDStr := ResolveFormID(oldRef);
    if newFormIDStr = '' then begin
      Inc(FailedCount);
      Continue;
    end;
    newFormID := StrToInt64('$' + newFormIDStr);
    
    w := ElementAssign(weathers, HighInteger, nil, False);
    if Assigned(w) then begin
      SetElementNativeValues(w, 'Weather', newFormID);
      SetElementNativeValues(w, 'Chance',
        RecordValueInt('Weather[' + IntToStr(i) + '].Chance'));
      Inc(LinkedCount);
    end else
      Inc(FailedCount);
  end;
end;

procedure RelinkQuestTargets(rec: IInterface);
var
  count, i: Integer;
  oldRef, newFormIDStr: string;
  newFormID: Cardinal;
  targets, target: IInterface;
begin
  count := RecordValueInt('TargetCount');
  if count <= 0 then Exit;
  
  targets := ElementByName(rec, 'Targets');
  if not Assigned(targets) then
    targets := Add(rec, 'Targets', True);
  
  for i := 0 to count - 1 do begin
    oldRef := RecordValue('Target[' + IntToStr(i) + '].Ref');
    newFormIDStr := ResolveFormID(oldRef);
    if newFormIDStr = '' then begin
      Inc(FailedCount);
      Continue;
    end;
    newFormID := StrToInt64('$' + newFormIDStr);
    
    target := ElementAssign(targets, HighInteger, nil, False);
    if Assigned(target) then begin
      SetElementNativeValues(target, 'QSTA\Target', newFormID);
      Inc(LinkedCount);
    end else
      Inc(FailedCount);
  end;
end;

procedure RelinkInfoTopics(rec: IInterface);
var
  count, i: Integer;
  oldRef: string;
  topics, topic: IInterface;
  newFormIDStr: string;
  newFormID: Cardinal;
begin
  count := RecordValueInt('AddTopicCount');
  if count <= 0 then Exit;
  
  topics := ElementByName(rec, 'Add Topics');
  if not Assigned(topics) then
    topics := Add(rec, 'Add Topics', True);
  
  for i := 0 to count - 1 do begin
    oldRef := RecordValue('AddTopic[' + IntToStr(i) + ']');
    newFormIDStr := ResolveFormID(oldRef);
    if newFormIDStr = '' then begin
      Inc(FailedCount);
      Continue;
    end;
    newFormID := StrToInt64('$' + newFormIDStr);
    
    topic := ElementAssign(topics, HighInteger, nil, False);
    if Assigned(topic) then begin
      SetEditValue(topic, IntToHex(newFormID, 8));
      Inc(LinkedCount);
    end else
      Inc(FailedCount);
  end;
end;

procedure RelinkLTEXGrasses(rec: IInterface);
var
  count, i: Integer;
  oldRef, newFormIDStr: string;
  newFormID: Cardinal;
  grasses, grass: IInterface;
begin
  count := RecordValueInt('GrassCount');
  if count <= 0 then Exit;
  
  grasses := ElementByName(rec, 'Grasses');
  if not Assigned(grasses) then
    grasses := Add(rec, 'Grasses', True);
  
  for i := 0 to count - 1 do begin
    oldRef := RecordValue('Grass[' + IntToStr(i) + ']');
    newFormIDStr := ResolveFormID(oldRef);
    if newFormIDStr = '' then begin
      Inc(FailedCount);
      Continue;
    end;
    newFormID := StrToInt64('$' + newFormIDStr);
    
    grass := ElementAssign(grasses, HighInteger, nil, False);
    if Assigned(grass) then begin
      SetEditValue(grass, IntToHex(newFormID, 8));
      Inc(LinkedCount);
    end else
      Inc(FailedCount);
  end;
end;

procedure RelinkLAND(rec: IInterface);
var
  i, layerCount, vtexCount: Integer;
  prefix, layerType, oldRef, newFormIDStr: string;
  newFormID: Cardinal;
  layers, layer, vtexArr, btxt, atxt: IInterface;
begin
  // Relink layer texture references (BTXT/ATXT -> LTEX)
  layerCount := RecordValueInt('LayerCount');
  if layerCount > 0 then begin
    layers := ElementByName(rec, 'Layers');
    if Assigned(layers) then begin
      for i := 0 to layerCount - 1 do begin
        if i >= ElementCount(layers) then Break;
        prefix := 'Layer[' + IntToStr(i) + ']';
        layerType := RecordValue(prefix + '.Type');
        layer := ElementByIndex(layers, i);

        if layerType = 'BASE' then begin
          btxt := ElementBySignature(layer, 'BTXT');
          if Assigned(btxt) then begin
            oldRef := RecordValue(prefix + '.BTXT.Texture');
            newFormIDStr := ResolveFormID(oldRef);
            if newFormIDStr <> '' then begin
              newFormID := StrToInt64('$' + newFormIDStr);
              SetElementNativeValues(btxt, 'Texture', newFormID);
              Inc(LinkedCount);
            end else
              Inc(FailedCount);
          end;
        end
        else if layerType = 'ALPHA' then begin
          atxt := ElementBySignature(layer, 'ATXT');
          if Assigned(atxt) then begin
            oldRef := RecordValue(prefix + '.ATXT.Texture');
            newFormIDStr := ResolveFormID(oldRef);
            if newFormIDStr <> '' then begin
              newFormID := StrToInt64('$' + newFormIDStr);
              SetElementNativeValues(atxt, 'Texture', newFormID);
              Inc(LinkedCount);
            end else
              Inc(FailedCount);
          end;
        end;
      end;
    end;
  end;

  // Relink VTEX entries
  vtexCount := RecordValueInt('VTEXCount');
  if vtexCount > 0 then begin
    vtexArr := ElementBySignature(rec, 'VTEX');
    if Assigned(vtexArr) then begin
      for i := 0 to vtexCount - 1 do begin
        if i < ElementCount(vtexArr) then begin
          oldRef := RecordValue('VTEX[' + IntToStr(i) + ']');
          newFormIDStr := ResolveFormID(oldRef);
          if newFormIDStr <> '' then begin
            newFormID := StrToInt64('$' + newFormIDStr);
            SetEditValue(ElementByIndex(vtexArr, i), IntToHex(newFormID, 8));
            Inc(LinkedCount);
          end else
            Inc(FailedCount);
        end;
      end;
    end;
  end;
end;

//============================================================================
// Per-Type Relink Dispatcher
//============================================================================

procedure RelinkRecord;
var
  targetType, oldFormID, newFormIDStr: string;
  newFormID: Cardinal;
  rec: IInterface;
  i: Integer;
begin
  targetType := RecordValue('TargetType');
  if targetType = '' then Exit;
  
  // Skip non-importable types
  if (targetType = 'PGRD_SKIP') or (targetType = 'ROAD_SKIP') or
     (targetType = 'SCPT_SOURCE') or (targetType = 'SKIL_REF') or
     (targetType = 'BSGN_SPELLS') or (targetType = 'UNKNOWN') then Exit;
  
  // Find the new record in TES5
  oldFormID := RecordValue('FormID');
  newFormIDStr := ResolveFormID(oldFormID);
  if newFormIDStr = '' then Exit;
  
  newFormID := StrToInt64('$' + newFormIDStr);
  // Search across all loaded files (target + masters)
  rec := nil;
  for i := 0 to FileCount - 1 do begin
    rec := RecordByFormID(FileByIndex(i), newFormID, True);
    if Assigned(rec) then Break;
  end;
  if not Assigned(rec) then Exit;
  
  // =========================
  // Placed objects
  // =========================
  if targetType = 'REFR' then begin
    SetRef(rec, 'NAME', RecordValue('NAME'));
    if RecordHasKey('XTEL.Door') then
      SetRef(rec, 'XTEL\Door', RecordValue('XTEL.Door'));
    if RecordHasKey('XLOC.Key') then
      SetRef(rec, 'XLOC\Key', RecordValue('XLOC.Key'));
    if RecordHasKey('XESP.Ref') then
      SetRef(rec, 'XESP\Reference', RecordValue('XESP.Ref'));
  end
  
  else if targetType = 'ACHR' then begin
    SetRef(rec, 'NAME', RecordValue('NAME'));
    if RecordHasKey('XESP.Ref') then
      SetRef(rec, 'XESP\Reference', RecordValue('XESP.Ref'));
  end
  
  // =========================
  // NPCs (NPC_ and converted CREA)
  // =========================
  else if targetType = 'NPC_' then begin
    if RecordHasKey('Race') then
      SetRef(rec, 'RNAM', RecordValue('Race'));
    if RecordHasKey('Class') then
      SetRef(rec, 'CNAM', RecordValue('Class'));
    if RecordHasKey('DeathItem') then
      SetRef(rec, 'INAM', RecordValue('DeathItem'));
    if RecordHasKey('CombatStyle') then
      SetRef(rec, 'ZNAM', RecordValue('CombatStyle'));
    if RecordHasKey('Hair') then
      SetRef(rec, 'HNAM', RecordValue('Hair'));
    RelinkItems(rec);
    RelinkSpells(rec);
    RelinkFactions(rec);
    RelinkAIPackages(rec);
  end
  
  // =========================
  // Containers
  // =========================
  else if targetType = 'CONT' then begin
    RelinkItems(rec);
  end
  
  // =========================
  // Leveled lists
  // =========================
  else if (targetType = 'LVLN') or (targetType = 'LVLI') or (targetType = 'LVSP') then begin
    RelinkLeveledEntries(rec);
    if RecordHasKey('Template') then
      SetRef(rec, 'TNAM', RecordValue('Template'));
  end
  
  // =========================
  // Factions
  // =========================
  else if targetType = 'FACT' then begin
    RelinkFactRelations(rec);
  end
  
  // =========================
  // Enchantments (on items)
  // =========================
  else if targetType = 'ARMO' then begin
    if RecordHasKey('ENAM') then
      SetRef(rec, 'EITM', RecordValue('ENAM'));
  end
  else if targetType = 'WEAP' then begin
    if RecordHasKey('ENAM') then
      SetRef(rec, 'EITM', RecordValue('ENAM'));
  end
  else if targetType = 'BOOK' then begin
    if RecordHasKey('ENAM') then
      SetRef(rec, 'EITM', RecordValue('ENAM'));
  end
  
  // =========================
  // Flora
  // =========================
  else if targetType = 'FLOR' then begin
    if RecordHasKey('Ingredient') then
      SetRef(rec, 'PFIG', RecordValue('Ingredient'));
  end
  
  // =========================
  // Idle Animations
  // =========================
  else if targetType = 'IDLE' then begin
    if RecordHasKey('DATA.Parent') then
      SetRef(rec, 'DATA\Parent', RecordValue('DATA.Parent'));
    if RecordHasKey('DATA.Previous') then
      SetRef(rec, 'DATA\Previous', RecordValue('DATA.Previous'));
  end
  
  // =========================
  // Magic Effects
  // =========================
  else if targetType = 'MGEF' then begin
    if RecordHasKey('DATA.Light') then
      SetRef(rec, 'DATA\Light', RecordValue('DATA.Light'));
    if RecordHasKey('DATA.EffectShader') then
      SetRef(rec, 'DATA\Effect Shader', RecordValue('DATA.EffectShader'));
    if RecordHasKey('DATA.CastingSound') then
      SetRef(rec, 'DATA\Casting Sound', RecordValue('DATA.CastingSound'));
    if RecordHasKey('DATA.BoltSound') then
      SetRef(rec, 'DATA\Bolt Sound', RecordValue('DATA.BoltSound'));
    if RecordHasKey('DATA.HitSound') then
      SetRef(rec, 'DATA\Hit Sound', RecordValue('DATA.HitSound'));
    if RecordHasKey('DATA.AreaSound') then
      SetRef(rec, 'DATA\Area Sound', RecordValue('DATA.AreaSound'));
  end
  
  // =========================
  // Climate
  // =========================
  else if targetType = 'CLMT' then begin
    RelinkClimateWeathers(rec);
  end
  
  // =========================
  // Landscape Texture
  // =========================
  else if targetType = 'LTEX' then begin
    RelinkLTEXGrasses(rec);
  end
  
  // =========================
  // Landscape (LAND)
  // =========================
  else if targetType = 'LAND' then begin
    RelinkLAND(rec);
  end
  
  // =========================
  // Race
  // =========================
  else if targetType = 'RACE' then begin
    RelinkSpells(rec);
  end
  
  // =========================
  // Quest
  // =========================
  else if targetType = 'QUST' then begin
    RelinkQuestTargets(rec);
  end
  
  // =========================
  // Region
  // =========================
  else if targetType = 'REGN' then begin
    if RecordHasKey('WNAM') then
      SetRef(rec, 'WNAM', RecordValue('WNAM'));
  end
  
  // =========================
  // Worldspace
  // =========================
  else if targetType = 'WRLD' then begin
    if RecordHasKey('WNAM.Parent') then
      SetRef(rec, 'WNAM', RecordValue('WNAM.Parent'));
    if RecordHasKey('CNAM.Climate') then
      SetRef(rec, 'CNAM', RecordValue('CNAM.Climate'));
    if RecordHasKey('NAM2.Water') then
      SetRef(rec, 'NAM2', RecordValue('NAM2.Water'));
  end
  
  // =========================
  // Dialog Info
  // =========================
  else if targetType = 'INFO' then begin
    if RecordHasKey('QSTI') then
      SetRef(rec, 'QSTI', RecordValue('QSTI'));
    if RecordHasKey('TPIC') then
      SetRef(rec, 'TPIC', RecordValue('TPIC'));
    if RecordHasKey('PNAM') then
      SetRef(rec, 'PNAM', RecordValue('PNAM'));
    RelinkInfoTopics(rec);
  end;
  
  // Other types with simple refs
  // AMMO enchantment
  if targetType = 'AMMO' then begin
    if RecordHasKey('ENAM') then
      SetRef(rec, 'EITM', RecordValue('ENAM'));
  end;
  
  // ACTI sound
  if targetType = 'ACTI' then begin
    if RecordHasKey('Sound') then
      SetRef(rec, 'SNAM', RecordValue('Sound'));
  end;
  
  // LIGH sound
  if targetType = 'LIGH' then begin
    if RecordHasKey('Sound') then
      SetRef(rec, 'SNAM', RecordValue('Sound'));
  end;
end;

//============================================================================
// Main Entry Point
//============================================================================

function Initialize: Integer;
var
  settingsFile, mappingFile, importFile: string;
  i: Integer;
  totalRecords: Integer;
  slSettings, slMasterMap: TStringList;
  masterParts: TStringList;
  masterFile: string;
begin
  Result := 0;
  ImportPath := DataPath + 'TES4Export\';
  mappingFile := ImportPath + 'FormID_Mapping.txt';
  importFile := ImportPath + 'TES4_Records.txt';
  
  // Check for automation settings file
  settingsFile := ScriptsPath + 'conversion_settings.txt';
  if FileExists(settingsFile) then begin
    slSettings := TStringList.Create;
    try
      slSettings.LoadFromFile(settingsFile);
      if slSettings.Values['MODE'] = 'RELINK' then begin
        if slSettings.Values['IMPORT_FILE'] <> '' then
          importFile := slSettings.Values['IMPORT_FILE'];
        if slSettings.Values['MAPPING_FILE'] <> '' then
          mappingFile := slSettings.Values['MAPPING_FILE'];
        if slSettings.Values['MASTER_MAPPINGS'] <> '' then begin
          // Will load additional master mappings after main mapping
        end;
        if slSettings.Values['OUTPUT_NAME'] <> '' then begin
          // Used for finding target plugin by name
        end;
      end;
    finally
      slSettings.Free;
    end;
  end;
  
  // Verify files exist
  if not FileExists(mappingFile) then begin
    AddMessage('ERROR: FormID mapping not found: ' + mappingFile);
    AddMessage('Run TES5_Import_Records.pas first to generate the mapping.');
    Result := 1;
    Exit;
  end;
  
  if not FileExists(importFile) then begin
    AddMessage('ERROR: Import file not found: ' + importFile);
    AddMessage('Run TES4_Export_Records.pas in TES4Edit first.');
    Result := 1;
    Exit;
  end;
  
  // Find target plugin
  TargetPlugin := nil;
  AutoOutputName := '';
  // In automation mode, look for specific output plugin
  if FileExists(settingsFile) then begin
    slSettings := TStringList.Create;
    try
      slSettings.LoadFromFile(settingsFile);
      if slSettings.Values['OUTPUT_NAME'] <> '' then begin
        AutoOutputName := slSettings.Values['OUTPUT_NAME'];
        for i := 0 to FileCount - 1 do begin
          if SameText(GetFileName(FileByIndex(i)), AutoOutputName) then begin
            TargetPlugin := FileByIndex(i);
            Break;
          end;
        end;
      end;
    finally
      slSettings.Free;
    end;
  end;
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
    AddMessage('ERROR: No target plugin found. The output plugin must be loaded.');
    AddMessage('Ensure the import phase has been run first and the plugin is selected in the module dialog.');
    Result := 1;
    Exit;
  end;
  
  AddMessage('TES5 Cross-Reference Relink: Starting...');
  AddMessage('Mapping file: ' + mappingFile);
  AddMessage('Export file: ' + importFile);
  AddMessage('Target plugin: ' + GetFileName(TargetPlugin));
  
  // Ensure all loaded files are registered as masters for cross-file references
  for i := 0 to FileCount - 1 do begin
    if FileByIndex(i) <> TargetPlugin then
      AddMasterIfMissing(TargetPlugin, GetFileName(FileByIndex(i)));
  end;
  
  // Load FormID mapping
  slMapping := TStringList.Create;
  slMapping.LoadFromFile(mappingFile);
  AddMessage('Loaded ' + IntToStr(slMapping.Count) + ' FormID mappings.');
  
  // Load master mappings if in automation mode
  if FileExists(settingsFile) then begin
    slSettings := TStringList.Create;
    try
      slSettings.LoadFromFile(settingsFile);
      if slSettings.Values['MASTER_MAPPINGS'] <> '' then begin
        masterParts := TStringList.Create;
        try
          masterParts.Delimiter := ';';
          masterParts.StrictDelimiter := True;
          masterParts.DelimitedText := slSettings.Values['MASTER_MAPPINGS'];
          for i := 0 to masterParts.Count - 1 do begin
            masterFile := masterParts[i];
            if (masterFile <> '') and FileExists(masterFile) then begin
              slMasterMap := TStringList.Create;
              try
                slMasterMap.LoadFromFile(masterFile);
                AddMessage('Loaded master mapping: ' + masterFile + ' (' + IntToStr(slMasterMap.Count) + ' entries)');
                slMapping.AddStrings(slMasterMap);
              finally
                slMasterMap.Free;
              end;
            end;
          end;
        finally
          masterParts.Free;
        end;
      end;
    finally
      slSettings.Free;
    end;
  end;
  
  // Load export data
  AddMessage('Loading export data from: ' + importFile);
  slImport := TStringList.Create;
  slImport.LoadFromFile(importFile);
  AddMessage('Loaded ' + IntToStr(slImport.Count) + ' lines of export data.');
  
  recData := TStringList.Create;
  LinkedCount := 0;
  FailedCount := 0;
  CurrentLine := 0;
  totalRecords := 0;
  
  AddMessage('Starting record processing loop...');
  while ReadNextRecord do begin
    RelinkRecord;
    Inc(totalRecords);
    if totalRecords mod 500 = 0 then
      AddMessage('  Processed ' + IntToStr(totalRecords) + ' records for relinking...');
  end;
  recData.Free;
  
  AddMessage('');
  AddMessage('TES5 Cross-Reference Relink: Complete!');
  AddMessage('Records processed: ' + IntToStr(totalRecords));
  AddMessage('References linked: ' + IntToStr(LinkedCount));
  AddMessage('References failed: ' + IntToStr(FailedCount));
  AddMessage('');
  AddMessage('=== REMAINING MANUAL TASKS ===');
  AddMessage('1. Magic effect references on ALCH/ENCH/INGR/SPEL need manual MGEF resolution');
  AddMessage('2. PACK records need complete manual rebuild (different system in TES5)');
  AddMessage('3. SCPT scripts must be rewritten in Papyrus');
  AddMessage('4. Any references that failed (count above) need manual fixup');
  AddMessage('5. References to Skyrim.esm base records (if needed) must be set manually');
  
  slMapping.Free;
  slImport.Free;
end;

function Process(e: IInterface): Integer;
begin
  Result := 0;
end;

function Finalize: Integer;
begin
  Result := 0;
end;

end.
