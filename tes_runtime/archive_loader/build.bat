@echo off
REM Build ArchiveLoader.dll into ..\dist. Needs only MSVC and the Windows SDK;
REM engine addresses resolve at runtime (Address Library, or ids_pre_ae.h on
REM SE 1.5.97 and VR).

setlocal
call "%~dp0..\common\msvc.bat"
if errorlevel 1 exit /b 1

cd /d "%~dp0"
if not exist obj mkdir obj
del /q obj\*.obj 2>nul

echo [build] compiling ArchiveLoader...
cl /nologo /c /EHa /std:c++17 /O2 /MD /W3 /DNDEBUG /I..\common ^
   plugin.cpp archives.cpp ^
   ..\common\addresses.cpp ..\common\hook.cpp ..\common\log.cpp ..\common\paths.cpp /Fo:obj\
if errorlevel 1 (
    echo [build] ERROR: ArchiveLoader compilation failed
    exit /b 1
)
link /nologo /DLL /OUT:..\dist\ArchiveLoader.dll /IMPLIB:obj\ArchiveLoader.lib obj\*.obj ^
     kernel32.lib user32.lib shell32.lib ole32.lib
if errorlevel 1 (
    echo [build] ERROR: ArchiveLoader link failed
    exit /b 1
)
echo [build] OK -^> %~dp0..\dist\ArchiveLoader.dll

endlocal
