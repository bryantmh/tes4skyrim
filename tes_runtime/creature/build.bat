@echo off
REM Build CreatureRuntime.dll into ..\dist, and compose_test.exe, which runs
REM the composer offline (tests/test_creature_anim.py diffs it against the
REM Python reference).
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

echo [build] compiling CreatureRuntime...
cl /nologo /c /EHa /std:c++17 /O2 /MD /W3 /DNDEBUG /I..\common ^
   plugin.cpp compose.cpp stream.cpp ^
   ..\common\addresses.cpp ..\common\hook.cpp ..\common\json.cpp ^
   ..\common\log.cpp ..\common\paths.cpp ^
   /Fo:obj\
if errorlevel 1 (
    echo [build] ERROR: CreatureRuntime compilation failed
    exit /b 1
)
link /nologo /DLL /OUT:..\dist\CreatureRuntime.dll /IMPLIB:obj\CreatureRuntime.lib obj\*.obj ^
     kernel32.lib shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: CreatureRuntime link failed
    exit /b 1
)
echo [build] OK -^> %~dp0..\dist\CreatureRuntime.dll

echo [build] compiling compose_test...
cl /nologo /EHa /std:c++17 /O2 /MD /W3 /DNDEBUG /I..\common ^
   compose_test.cpp compose.cpp ..\common\json.cpp ..\common\log.cpp ..\common\paths.cpp ^
   /Fo:objt\ /Fe:compose_test.exe /link shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: compose_test failed
    exit /b 1
)
echo [build] OK -^> %~dp0compose_test.exe

endlocal
