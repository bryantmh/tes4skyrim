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
REM         build.bat test       the headless gates: store, filter, session,
REM                              script, alchemy and journal log

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
REM /DNOMINMAX: windows.h defines min/max as MACROS, which breaks every
REM std::min / std::max the vendored OpenMW headers use the moment a
REM translation unit includes both. See esm/esmcommon.hpp:52.
set CXXFLAGS=/nologo /c /EHsc /std:c++20 /permissive- /Zc:__cplusplus /O2 /MD /W3 /DNDEBUG /DNOMINMAX
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
   plugin\script_runner.cpp plugin\script_ops_world.cpp ^
   plugin\script_ops_events.cpp plugin\script_ops_sound.cpp ^
   plugin\script_ops_move.cpp plugin\script_ops_ai.cpp ^
   plugin\script_ops_query.cpp plugin\script_ops_stats.cpp ^
   plugin\script_ops_spell.cpp plugin\script_ops_control.cpp ^
   plugin\object_script.cpp ^
   plugin\object_tick.cpp ^
   plugin\script_tables.cpp plugin\persuasion.cpp ^
   plugin\conversation_persuasion.cpp plugin\conversation_modal.cpp ^
   plugin\conversation_travel.cpp plugin\travel.cpp ^
   plugin\game_calls.cpp plugin\game_calls_move.cpp plugin\game_calls_ai.cpp ^
   plugin\game_calls_query.cpp plugin\game_calls_spell.cpp ^
   plugin\game_calls_message.cpp plugin\game_calls_control.cpp ^
   plugin\game_calls_state.cpp plugin\game_calls_crime.cpp ^
   plugin\alchemy.cpp plugin\alchemy_hooks.cpp ^
   plugin\journal_log.cpp plugin\journal_objectives.cpp ^
   plugin\cosave.cpp plugin\main_thread.cpp /Fo:obj\
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
   plugin\script_runner.cpp plugin\script_ops_world.cpp ^
   plugin\script_ops_events.cpp plugin\script_ops_sound.cpp ^
   plugin\script_ops_move.cpp plugin\script_ops_ai.cpp ^
   plugin\script_ops_query.cpp plugin\script_ops_stats.cpp ^
   plugin\script_ops_spell.cpp plugin\script_ops_control.cpp ^
   plugin\object_script.cpp ^
   plugin\object_tick.cpp plugin\main_thread.cpp ^
   plugin\script_tables.cpp plugin\persuasion.cpp ^
   plugin\travel.cpp plugin\script_test.cpp ^
   plugin\alchemy.cpp plugin\alchemy_test.cpp ^
   plugin\journal_log.cpp plugin\journal_log_test.cpp /Fo:objt\
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
     objt\dialogue_state.obj objt\game_actor.obj ^
     objt\script_runner.obj objt\script_ops_world.obj ^
     objt\script_ops_events.obj objt\script_ops_sound.obj ^
     objt\script_ops_move.obj objt\script_ops_ai.obj ^
     objt\script_ops_query.obj objt\script_ops_stats.obj ^
     objt\script_ops_spell.obj objt\script_ops_control.obj ^
     objt\object_script.obj ^
     objt\object_tick.obj objt\main_thread.obj ^
     objt\persuasion.obj obj\mw\*.obj ^
     kernel32.lib user32.lib shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: session_test link failed
    exit /b 1
)
link /nologo /OUT:script_test.exe objt\store.obj objt\log.obj ^
     objt\game_actor.obj objt\dialogue_state.obj objt\script_context.obj ^
     objt\script_runner.obj objt\script_ops_world.obj ^
     objt\script_ops_events.obj objt\script_ops_sound.obj ^
     objt\script_ops_move.obj objt\script_ops_ai.obj ^
     objt\script_ops_query.obj objt\script_ops_stats.obj ^
     objt\script_ops_spell.obj objt\script_ops_control.obj ^
     objt\object_script.obj ^
     objt\object_tick.obj objt\main_thread.obj ^
     objt\script_tables.obj objt\persuasion.obj objt\travel.obj ^
     objt\script_test.obj ^
     objt\filter.obj ^
     obj\mw\*.obj ^
     kernel32.lib user32.lib shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: script_test link failed
    exit /b 1
)
link /nologo /OUT:alchemy_test.exe objt\alchemy.obj objt\alchemy_test.obj ^
     objt\store.obj objt\log.obj objt\script_tables.obj ^
     kernel32.lib user32.lib shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: alchemy_test link failed
    exit /b 1
)
link /nologo /OUT:journal_log_test.exe objt\journal_log.obj ^
     objt\journal_log_test.obj kernel32.lib
if errorlevel 1 (
    echo [build] ERROR: journal_log_test link failed
    exit /b 1
)
echo [build] OK -^> %~dp0store_test.exe, filter_test.exe, session_test.exe, script_test.exe, alchemy_test.exe, journal_log_test.exe

:done

endlocal
