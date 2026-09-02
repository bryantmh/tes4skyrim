Scriptname TES4SKSE Hidden

; Normalize a base form or a placed reference to its base form before calling
; SKSE64 Form natives. Oblivion's single ref type accepted both shapes.
Form Function GetBaseForm(Form akForm) Global
  ObjectReference placed = akForm as ObjectReference
  If placed
    Return placed.GetBaseObject()
  EndIf
  Return akForm
EndFunction
