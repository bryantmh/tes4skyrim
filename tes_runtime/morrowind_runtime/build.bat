@echo off
REM Build MorrowindRuntime.dll -- a TESRuntime SUBMODULE, like havok_world_size.
REM
REM Its own source folder and its own DLL, sharing no code with plugin\, for
REM two reasons: it links vendored GPL-3.0 OpenMW that must stay out of
REM TESRuntime's binary, and a fault in a script interpreter must not take gun
REM routing, limb severing or the animation cache down with it. Both DLLs ship
REM in the same TESRuntime.zip.
REM See docs/commentary/morrowind_runtime.md#licensing
REM
REM Standalone build, same as its parent: no SKSE source tree, no CMake, no
REM vcpkg. Everything from the game resolves at runtime through the Address
REM Library, so the only inputs are MSVC and the Windows SDK.
REM
REM Usage:  build.bat            full plugin -> MorrowindRuntime.dll
REM         build.bat openmw     compile the vendored OpenMW subset ONLY
REM                              (the Phase 0 gate: does it build standalone?)
REM         build.bat test       store_test.exe, the headless parser gate

setlocal

set VS=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools
call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
if errorlevel 1 (
    echo [build] ERROR: could not initialise MSVC x64 environment
    exit /b 1
)

set ROOT=%~dp0..\..
set MW=%ROOT%\external\openmw

REM OpenMW is C++20 upstream. /permissive- and /Zc:__cplusplus are required for
REM the standard-conformance the sources assume; /EHsc because the interpreter
REM uses exceptions for script errors.
set CXXFLAGS=/nologo /c /EHsc /std:c++20 /permissive- /Zc:__cplusplus /O2 /MD /W3 /DNDEBUG
set INCLUDES=/I"%MW%" /I"%MW%\apps"

cd /d "%~dp0"
if not exist obj mkdir obj
if not exist obj\mw mkdir obj\mw

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
   /Fo:obj\mw\
if errorlevel 1 (
    echo [build] ERROR: vendored OpenMW failed to compile
    exit /b 1
)
echo [build] OK -^> vendored OpenMW compiles standalone

if /i "%~1"=="openmw" goto done
if /i "%~1"=="test" goto storetest

REM Named rather than plugin\*.cpp: store_test.cpp carries a main() and is
REM built only by `build.bat test`.
echo [build] compiling plugin...
cl %CXXFLAGS% %INCLUDES% plugin\plugin.cpp plugin\store.cpp ^
   plugin\log.cpp plugin\addresses.cpp plugin\menu.cpp ^
   plugin\filter.cpp plugin\session.cpp plugin\activation.cpp ^
   plugin\game_actor.cpp plugin\conversation.cpp ^
   plugin\script_context.cpp plugin\dialogue_state.cpp ^
   plugin\script_runner.cpp plugin\script_tables.cpp ^
   plugin\game_calls.cpp plugin\cosave.cpp plugin\main_thread.cpp /Fo:obj\
if errorlevel 1 (
    echo [build] ERROR: plugin compilation failed
    exit /b 1
)

echo [build] linking...
link /nologo /DLL /OUT:MorrowindRuntime.dll obj\*.obj obj\mw\*.obj ^
     kernel32.lib user32.lib shell32.lib ole32.lib advapi32.lib
if errorlevel 1 (
    echo [build] ERROR: link failed
    exit /b 1
)
echo [build] OK -^> %~dp0MorrowindRuntime.dll
goto done

REM The store parses export text and touches no game memory, so it is testable
REM with no Skyrim and no SKSE. store.cpp is compiled again here rather than
REM reused from obj\, which holds the DLL's objects.
:storetest
if not exist objt mkdir objt
echo [build] compiling tests...
cl %CXXFLAGS% %INCLUDES% plugin\store.cpp plugin\log.cpp plugin\filter.cpp ^
   plugin\session.cpp plugin\store_test.cpp plugin\filter_test.cpp ^
   plugin\session_test.cpp plugin\game_actor.cpp ^
   plugin\dialogue_state.cpp plugin\script_context.cpp ^
   plugin\script_runner.cpp plugin\script_tables.cpp ^
   plugin\script_test.cpp /Fo:objt\
if errorlevel 1 (
    echo [build] ERROR: test compilation failed
    exit /b 1
)
link /nologo /OUT:store_test.exe objt\store.obj objt\log.obj ^
     objt\script_tables.obj objt\store_test.obj kernel32.lib user32.lib shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: store_test link failed
    exit /b 1
)
link /nologo /OUT:filter_test.exe objt\store.obj objt\log.obj ^
     objt\script_tables.obj objt\filter.obj objt\filter_test.obj ^
     kernel32.lib user32.lib shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: filter_test link failed
    exit /b 1
)
link /nologo /OUT:session_test.exe objt\store.obj objt\log.obj ^
     objt\script_tables.obj objt\filter.obj objt\session.obj ^
     objt\session_test.obj objt\script_context.obj ^
     objt\dialogue_state.obj obj\mw\*.obj ^
     kernel32.lib user32.lib shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: session_test link failed
    exit /b 1
)
link /nologo /OUT:script_test.exe objt\store.obj objt\log.obj ^
     objt\game_actor.obj objt\dialogue_state.obj objt\script_context.obj ^
     objt\script_runner.obj objt\script_tables.obj objt\script_test.obj ^
     objt\filter.obj ^
     obj\mw\*.obj ^
     kernel32.lib user32.lib shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: script_test link failed
    exit /b 1
)
echo [build] OK -^> %~dp0store_test.exe, filter_test.exe, session_test.exe, script_test.exe

:done

endlocal
