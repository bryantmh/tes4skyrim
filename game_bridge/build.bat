@echo off
REM Build TESGameBridge.dll (SKSE plugin, x64).
REM
REM Standalone build: no SKSE source tree, no CMake, no vcpkg. Everything the
REM plugin needs from the game is resolved at runtime, so the only inputs are
REM MSVC and the Windows SDK.
REM
REM Usage:  build.bat [deploy]
REM   deploy -- also copy the DLL into the Skyrim SE Data\SKSE\Plugins folder

setlocal

set VS=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools
call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
if errorlevel 1 (
    echo [build] ERROR: could not initialise MSVC x64 environment
    exit /b 1
)

cd /d "%~dp0plugin"
if not exist obj mkdir obj

echo [build] compiling...
cl /nologo /c /EHa /std:c++17 /O2 /MD /W3 /DNDEBUG ^
   plugin.cpp commands.cpp console_exec.cpp console_capture.cpp ^
   papyrus_capture.cpp detour.cpp rawmem.cpp generic_hook.cpp script_object.cpp trace.cpp ^
   game.cpp addresses.cpp pipe_server.cpp main_thread.cpp json.cpp log.cpp ^
   /Fo:obj\
if errorlevel 1 (
    echo [build] ERROR: compilation failed
    exit /b 1
)

echo [build] linking...
link /nologo /DLL /OUT:..\TESGameBridge.dll obj\*.obj ^
     kernel32.lib user32.lib shell32.lib ole32.lib advapi32.lib winmm.lib
if errorlevel 1 (
    echo [build] ERROR: link failed
    exit /b 1
)

echo [build] OK -^> %~dp0TESGameBridge.dll

REM NOTE: the destination path contains parentheses, which cmd parses as block
REM delimiters inside an if(...) body. Quote the whole assignment and keep the
REM copy out of a parenthesised block.
if /i not "%~1"=="deploy" goto :done

set "DEST=C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data\SKSE\Plugins"
echo [build] deploying to "%DEST%"
copy /y "%~dp0TESGameBridge.dll" "%DEST%\TESGameBridge.dll" >nul
if errorlevel 1 echo [build] ERROR: deploy failed ^(game running, or needs admin?^) & exit /b 1
echo [build] deployed

:done

endlocal
