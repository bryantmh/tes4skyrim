@echo off
rem Build the SpeedTree ground-truth harness.
rem
rem 32-BIT on purpose: Oblivion.exe is i386 with its relocations STRIPPED, so
rem it can only be mapped at its fixed image base 0x400000, and only a 32-bit
rem host can address that.
rem
rem The built .exe is COMMITTED to native/dist/ (see that README): the
rem conversion needs it and most machines running this have no C++ compiler.
rem Invoked by `python native/build.py --programs`, or directly.
setlocal enabledelayedexpansion

rem Locate MSVC through vswhere rather than a hardcoded version directory --
rem the previous hardcoded "Visual Studio\18\BuildTools" path only existed on
rem one machine.  Build Tools are enough; a full Visual Studio is not required.
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
set "VCVARS="
if exist "%VSWHERE%" (
  for /f "usebackq delims=" %%I in (`"%VSWHERE%" -all -products * -property installationPath`) do (
    if exist "%%I\VC\Auxiliary\Build\vcvarsall.bat" set "VCVARS=%%I\VC\Auxiliary\Build\vcvarsall.bat"
  )
)
if not defined VCVARS (
  echo ERROR: MSVC not found via vswhere.
  echo Install "Build Tools for Visual Studio" with the C++ workload,
  echo including the x86 target.
  exit /b 1
)

rem vcvarsall.bat shells out to vswhere.exe by BARE NAME, so the VS Installer
rem directory must be on PATH or it prints "not recognized" and can leave the
rem environment half-initialised (build.py hits the same thing).
set "PATH=%PATH%;%ProgramFiles(x86)%\Microsoft Visual Studio\Installer"
call "%VCVARS%" x86 >nul || exit /b 1

rem Source lives here; the artifact goes to native/dist/ alongside the
rem committed .pyd.  %~dp0 is native/src/spt_engine/.
rem An optional first argument overrides the output directory (e.g. a scratch
rem folder to test a build before replacing the committed one).
set "SRC=%~dp0spt_engine_dump.cpp"
set "OUTDIR=%~dp0..\..\dist"
if not "%~1"=="" set "OUTDIR=%~1"
set "OBJDIR=%~dp0..\..\build\spt_engine"
if not exist "%OUTDIR%" mkdir "%OUTDIR%"
if not exist "%OBJDIR%" mkdir "%OBJDIR%"

rc /nologo /fo"%OBJDIR%\spt_engine_dump.res" "%~dp0spt_engine_dump.rc" || exit /b 1

rem /BASE:0x200000 /FIXED /DYNAMICBASE:NO: puts the host's own uninitialised
rem         .oblimg section over 0x400000, where the mapped Oblivion.exe must
rem         sit (its relocations are stripped) -- see the comment above
rem         g_image_space.  The loader maps the exe before anything else, so
rem         nothing can take that range first.
rem /SAFESEH:NO: Oblivion's code now lives INSIDE our image, so its SEH
rem         handlers would be rejected for missing from our SafeSEH table.
rem /GS-  :the host's own stack-guard fires when engine code returns through
rem         our frames; its __report_gsfailure calls ExitProcess(0xC000000D),
rem         which reads exactly like an engine crash.  We call foreign code by
rem         design, so the guard is noise here.
rem /EHa  : engine functions raise SEH; __try must be able to catch it.
rem /MANIFEST:EMBED + asInvoker: a normal, non-elevated app manifest.
cl /nologo /O2 /EHa /GS- /std:c++17 "%SRC%" /Fo"%OBJDIR%\\" ^
   /Fe:"%OUTDIR%\spt_engine_dump.exe" ^
   /link "%OBJDIR%\spt_engine_dump.res" ^
   /BASE:0x200000 /FIXED /DYNAMICBASE:NO /SAFESEH:NO ^
   /MANIFEST:EMBED /MANIFESTUAC:"level='asInvoker' uiAccess='false'"
set RC=%ERRORLEVEL%
if not "%RC%"=="0" exit /b %RC%
echo built %OUTDIR%\spt_engine_dump.exe
