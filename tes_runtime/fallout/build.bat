@echo off
REM Build FalloutRuntime.dll into ..\dist.
REM
REM Standalone: no SKSE source tree, no CMake. Everything the plugin needs from
REM the game resolves at runtime through the Address Library, so the only
REM inputs are MSVC and the Windows SDK. sever.cpp is compiled but dormant
REM (plugin.cpp installs none of it).

setlocal
call "%~dp0..\common\msvc.bat"
if errorlevel 1 exit /b 1

cd /d "%~dp0"
if not exist obj mkdir obj
del /q obj\*.obj 2>nul

echo [build] compiling FalloutRuntime...
cl /nologo /c /EHa /std:c++17 /O2 /MD /W3 /DNDEBUG /I..\common ^
   plugin.cpp guns.cpp fire.cpp hud.cpp parts.cpp zoom.cpp sever.cpp ^
   ..\common\addresses.cpp ..\common\engine.cpp ..\common\hook.cpp ^
   ..\common\json.cpp ..\common\log.cpp ..\common\paths.cpp ^
   /Fo:obj\
if errorlevel 1 (
    echo [build] ERROR: FalloutRuntime compilation failed
    exit /b 1
)
link /nologo /DLL /OUT:..\dist\FalloutRuntime.dll /IMPLIB:obj\FalloutRuntime.lib obj\*.obj ^
     kernel32.lib user32.lib shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: FalloutRuntime link failed
    exit /b 1
)
echo [build] OK -^> %~dp0..\dist\FalloutRuntime.dll

endlocal
