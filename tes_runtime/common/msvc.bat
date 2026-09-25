@echo off
REM Puts the MSVC x64 toolchain on the calling script's environment. Every
REM project's build.bat calls this first; it does nothing when the toolchain
REM is already set up (tes_runtime\build.bat sets it up once for all of them).

if defined VSCMD_VER exit /b 0
set VS=C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools
call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
if errorlevel 1 (
    echo [build] ERROR: could not initialise MSVC x64 environment
    exit /b 1
)
exit /b 0
