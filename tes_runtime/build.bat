@echo off
REM Build TESRuntime.dll (SKSE plugin, x64) and compose_test.exe.
REM
REM Standalone build, same as game_bridge: no SKSE source tree, no CMake.
REM Everything the plugin needs from the game is resolved at runtime through
REM the Address Library, so the only inputs are MSVC and the Windows SDK.
REM
REM Usage:  build.bat              full plugin -> TESRuntime.dll
REM         build.bat cache-only   animation cache composition ONLY, with
REM                                gun routing and limb severing not compiled
REM                                in, -> TESRuntime_CacheOnly.dll

setlocal

set VS=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools
call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
if errorlevel 1 (
    echo [build] ERROR: could not initialise MSVC x64 environment
    exit /b 1
)

cd /d "%~dp0plugin"
if not exist obj mkdir obj
if not exist objt mkdir objt

if /i "%~1"=="cache-only" goto cacheonly

echo [build] compiling plugin...
cl /nologo /c /EHa /std:c++17 /O2 /MD /W3 /DNDEBUG ^
   plugin.cpp addresses.cpp hook.cpp stream.cpp compose.cpp json.cpp log.cpp ^
   engine.cpp guns.cpp fire.cpp hud.cpp parts.cpp zoom.cpp sever.cpp ^
   /Fo:obj\
if errorlevel 1 (
    echo [build] ERROR: compilation failed
    exit /b 1
)

echo [build] linking plugin...
link /nologo /DLL /OUT:..\TESRuntime.dll obj\*.obj kernel32.lib user32.lib shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: link failed
    exit /b 1
)
echo [build] OK -^> %~dp0TESRuntime.dll

REM HavokWorldSize is its OWN DLL from its OWN source folder: it shares no code
REM with the plugin and needs neither the Address Library nor any engine
REM contract, so a fault in it must not take TESRuntime down with it.  Built
REM here so both ship together.
pushd "%~dp0havok_world_size"
if not exist obj mkdir obj
echo [build] compiling HavokWorldSize...
cl /nologo /LD /EHsc /std:c++17 /O2 /MD /W3 /DNDEBUG ^
   havok_world_size.cpp /Fo:obj\ /Fe:..\HavokWorldSize.dll ^
   /link /IMPLIB:obj\HavokWorldSize.lib /OPT:REF /OPT:ICF
if errorlevel 1 (
    echo [build] ERROR: HavokWorldSize failed
    popd
    exit /b 1
)
popd
echo [build] OK -^> %~dp0HavokWorldSize.dll

REM MorrowindRuntime is the other submodule, built by its own script because it
REM needs C++20 and the vendored OpenMW include paths. Its failure is NOT fatal
REM here: TESRuntime ships with or without it.
call "%~dp0morrowind_runtime\build.bat"
if errorlevel 1 echo [build] WARNING: MorrowindRuntime did not build

goto composetest

:cacheonly
REM engine/guns/sever are not compiled at all: the cache path needs three
REM Address Library ids and no form, native or co-save.
if not exist objc mkdir objc
echo [build] compiling plugin (cache-only)...
cl /nologo /c /EHa /std:c++17 /O2 /MD /W3 /DNDEBUG /DTESRUNTIME_CACHE_ONLY ^
   plugin.cpp addresses.cpp hook.cpp stream.cpp compose.cpp json.cpp log.cpp ^
   /Fo:objc\
if errorlevel 1 (
    echo [build] ERROR: compilation failed
    exit /b 1
)

echo [build] linking plugin (cache-only)...
link /nologo /DLL /OUT:..\TESRuntime_CacheOnly.dll objc\*.obj ^
   kernel32.lib shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: link failed
    exit /b 1
)
echo [build] OK -^> %~dp0TESRuntime_CacheOnly.dll

:composetest

echo [build] compiling compose_test...
cl /nologo /EHa /std:c++17 /O2 /MD /W3 /DNDEBUG ^
   compose_test.cpp compose.cpp json.cpp /Fo:objt\ /Fe:..\compose_test.exe
if errorlevel 1 (
    echo [build] ERROR: compose_test failed
    exit /b 1
)
echo [build] OK -^> %~dp0compose_test.exe

endlocal
