@echo off
rem Build the TESRACT launcher (64-bit, static runtime: the release folder's top holds no DLLs).
rem Invoked by tools/release/build_exe.py with the output folder as the first argument.
rem See docs/commentary/core_gui.md#folder-build
setlocal

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
set "VCVARS="
if exist "%VSWHERE%" (
  for /f "usebackq delims=" %%I in (`"%VSWHERE%" -all -products * -property installationPath`) do (
    if exist "%%I\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=%%I\VC\Auxiliary\Build\vcvars64.bat"
  )
)
if not defined VCVARS (
  echo ERROR: MSVC not found via vswhere. Install "Build Tools for Visual Studio" with the C++ workload.
  exit /b 1
)
rem vcvars64.bat runs vswhere.exe by bare name, so its folder must be on PATH.
set "PATH=%PATH%;%ProgramFiles(x86)%\Microsoft Visual Studio\Installer"
call "%VCVARS%" >nul || exit /b 1

set "OUTDIR=%~1"
if "%OUTDIR%"=="" set "OUTDIR=%~dp0..\..\..\build\launcher"
set "OBJDIR=%~dp0..\..\build\launcher"
if not exist "%OUTDIR%" mkdir "%OUTDIR%"
if not exist "%OBJDIR%" mkdir "%OBJDIR%"

pushd "%~dp0"
rc /nologo /fo"%OBJDIR%\launcher.res" launcher.rc || (popd & exit /b 1)
popd
cl /nologo /O2 /MT /EHsc /std:c++17 /W3 /DUNICODE /D_UNICODE "%~dp0launcher.cpp" /Fo"%OBJDIR%\\" ^
   /Fe:"%OUTDIR%\TESRACT.exe" ^
   /link "%OBJDIR%\launcher.res" user32.lib gdi32.lib /SUBSYSTEM:WINDOWS /MANIFEST:NO
exit /b %ERRORLEVEL%
