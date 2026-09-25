@echo off
REM Build TESRuntime.dll into ..\dist, and journal_log_test.exe, the headless
REM gate for the journal record store.
REM
REM Standalone: no SKSE source tree, no CMake. Everything the plugin needs from
REM the game resolves at runtime through the Address Library, so the only
REM inputs are MSVC and the Windows SDK.

setlocal
call "%~dp0..\common\msvc.bat"
if errorlevel 1 exit /b 1

cd /d "%~dp0"
if not exist obj mkdir obj
if not exist objt mkdir objt
del /q obj\*.obj 2>nul

echo [build] compiling TESRuntime...
cl /nologo /c /EHa /std:c++17 /O2 /MD /W3 /DNDEBUG /I..\common ^
   plugin.cpp crime.cpp journal_objectives.cpp journal_log.cpp ^
   ..\common\addresses.cpp ..\common\engine.cpp ..\common\hook.cpp ^
   ..\common\json.cpp ..\common\log.cpp ..\common\paths.cpp ^
   /Fo:obj\
if errorlevel 1 (
    echo [build] ERROR: TESRuntime compilation failed
    exit /b 1
)
link /nologo /DLL /OUT:..\dist\TESRuntime.dll /IMPLIB:obj\TESRuntime.lib obj\*.obj ^
     kernel32.lib user32.lib shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: TESRuntime link failed
    exit /b 1
)
echo [build] OK -^> %~dp0..\dist\TESRuntime.dll

echo [build] compiling journal_log_test...
cl /nologo /EHa /std:c++17 /O2 /MD /W3 /DNDEBUG ^
   journal_log.cpp journal_log_test.cpp /Fo:objt\ /Fe:journal_log_test.exe
if errorlevel 1 (
    echo [build] ERROR: journal_log_test failed
    exit /b 1
)
echo [build] OK -^> %~dp0journal_log_test.exe

endlocal
