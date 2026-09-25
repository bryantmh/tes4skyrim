@echo off
REM Build every runtime DLL into dist\: TESRuntime, CreatureRuntime,
REM FalloutRuntime, HavokWorldSize and MorrowindRuntime. Each project also
REM builds alone with its own build.bat.
REM
REM A failed project does not stop the others; the exit code is 1 when any
REM failed, and the failures are listed at the end.

setlocal EnableDelayedExpansion
call "%~dp0common\msvc.bat"
if errorlevel 1 exit /b 1
if not exist "%~dp0dist" mkdir "%~dp0dist"

set FAILED=
for %%P in (tes creature fallout havok_world_size morrowind) do (
    echo.
    echo [build] ==== %%P ====
    call "%~dp0%%P\build.bat"
    if errorlevel 1 set FAILED=!FAILED! %%P
)

echo.
if defined FAILED (
    echo [build] FAILED:!FAILED!
    exit /b 1
)
echo [build] all runtimes built -^> %~dp0dist
endlocal
