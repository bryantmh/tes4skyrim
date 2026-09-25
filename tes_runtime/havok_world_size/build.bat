@echo off
REM Build HavokWorldSize.dll into ..\dist.
REM
REM It shares no code with the other runtimes and needs neither the Address
REM Library nor any engine contract, so it compiles alone.

setlocal
call "%~dp0..\common\msvc.bat"
if errorlevel 1 exit /b 1

cd /d "%~dp0"
if not exist obj mkdir obj

echo [build] compiling HavokWorldSize...
cl /nologo /LD /EHsc /std:c++17 /O2 /MD /W3 /DNDEBUG ^
   havok_world_size.cpp /Fo:obj\ /Fe:..\dist\HavokWorldSize.dll ^
   /link /IMPLIB:obj\HavokWorldSize.lib /OPT:REF /OPT:ICF
if errorlevel 1 (
    echo [build] ERROR: HavokWorldSize failed
    exit /b 1
)
echo [build] OK -^> %~dp0..\dist\HavokWorldSize.dll

endlocal
