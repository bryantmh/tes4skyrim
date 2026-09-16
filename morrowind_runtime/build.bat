@echo off
REM Build MorrowindRuntime.dll (SKSE plugin, x64).
REM
REM Standalone build, same as tes_runtime and game_bridge: no SKSE source tree,
REM no CMake, no vcpkg. Everything the plugin needs from the game is resolved at
REM runtime through the Address Library, so the only inputs are MSVC and the
REM Windows SDK.
REM
REM This is a SEPARATE DLL from TESRuntime.dll on purpose: it links vendored
REM GPL-3.0 OpenMW code, and a fault in a 458-opcode interpreter must not take
REM gun routing, limb severing or the animation cache down with it.
REM See docs/commentary/morrowind_runtime.md#licensing
REM
REM Usage:  build.bat            full plugin -> MorrowindRuntime.dll
REM         build.bat openmw     compile the vendored OpenMW subset ONLY
REM                              (the Phase 0 gate: does it build standalone?)

setlocal

set VS=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools
call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
if errorlevel 1 (
    echo [build] ERROR: could not initialise MSVC x64 environment
    exit /b 1
)

set ROOT=%~dp0..
set MW=%ROOT%\external\openmw

REM OpenMW is C++20 upstream. /permissive- and /Zc:__cplusplus are required for
REM the standard-conformance the sources assume; /EHsc because the interpreter
REM uses exceptions for script errors.
set CXXFLAGS=/nologo /c /EHsc /std:c++20 /permissive- /Zc:__cplusplus /O2 /MD /W3 /DNDEBUG
set INCLUDES=/I"%MW%" /I"%MW%\apps"

cd /d "%~dp0"
if not exist obj mkdir obj

REM misc/strings and esm4 are header-only in this closure; esm/ contributes the
REM RefId translation units refid.hpp pulls in.
echo [build] compiling vendored OpenMW (interpreter + compiler)...
cl %CXXFLAGS% %INCLUDES% ^
   "%MW%\components\interpreter\*.cpp" ^
   "%MW%\components\compiler\*.cpp" ^
   "%MW%\components\esm\*.cpp" ^
   "%MW%\components\debug\debuglog.cpp" ^
   "%MW%\components\files\conversion.cpp" ^
   "%MW%\components\misc\*.cpp" ^
   /Fo:obj\
if errorlevel 1 (
    echo [build] ERROR: vendored OpenMW failed to compile
    exit /b 1
)
echo [build] OK -^> vendored OpenMW compiles standalone

if /i "%~1"=="openmw" goto done

echo [build] compiling plugin...
cl %CXXFLAGS% %INCLUDES% plugin\*.cpp /Fo:obj\
if errorlevel 1 (
    echo [build] ERROR: plugin compilation failed
    exit /b 1
)

echo [build] linking...
link /nologo /DLL /OUT:MorrowindRuntime.dll obj\*.obj ^
     kernel32.lib user32.lib shell32.lib ole32.lib advapi32.lib
if errorlevel 1 (
    echo [build] ERROR: link failed
    exit /b 1
)
echo [build] OK -^> %~dp0MorrowindRuntime.dll

:done

endlocal
