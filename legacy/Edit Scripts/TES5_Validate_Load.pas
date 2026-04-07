unit TES5_Validate_Load;

{
  Quick validation script — just loads the plugin and reports basic stats.
  Run in SSEEdit to verify that a converted plugin loads without errors.
}

var
  RecordCount: Integer;
  ErrorCount: Integer;

function Initialize: Integer;
begin
  Result := 0;
  RecordCount := 0;
  ErrorCount := 0;
  AddMessage('=== TES5 Validate Load ===');
  AddMessage('Checking loaded files...');
end;

function Process(e: IInterface): Integer;
begin
  Result := 0;
  Inc(RecordCount);
end;

function Finalize: Integer;
var
  i: Integer;
  f: IInterface;
begin
  Result := 0;
  AddMessage('');
  AddMessage('Loaded files:');
  for i := 0 to Pred(FileCount) do begin
    f := FileByIndex(i);
    AddMessage('  [' + IntToStr(i) + '] ' + GetFileName(f) +
               ' (' + IntToStr(RecordCount) + ' records processed)');
  end;
  AddMessage('');
  AddMessage('Validation complete. If you see this, the plugin loaded successfully.');
  AddMessage('Records processed: ' + IntToStr(RecordCount));
end;

end.
