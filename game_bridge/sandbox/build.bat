@echo off
REM Build TESSandbox.dll: the folder redirection a headless test instance is
REM injected with before SKSE. Not an SKSE plugin and never shipped.
REM See docs/commentary/headless_testing.md#sandbox

setlocal
call "%~dp0..\..\tes_runtime\common\msvc.bat"
if errorlevel 1 exit /b 1

cd /d "%~dp0"
if not exist obj mkdir obj
cl /nologo /c /EHsc /std:c++17 /O2 /MD /W3 /DNDEBUG sandbox.cpp /Fo:obj\
if errorlevel 1 (
    echo [build] ERROR: compilation failed
    exit /b 1
)
link /nologo /DLL /OUT:TESSandbox.dll obj\sandbox.obj kernel32.lib shell32.lib ole32.lib uuid.lib
if errorlevel 1 (
    echo [build] ERROR: link failed
    exit /b 1
)
echo [build] OK -^> %~dp0TESSandbox.dll
