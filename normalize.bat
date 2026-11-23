@echo off
rem Launcher for Windows.
rem   Double-click normalize.bat        -> opens the window
rem   normalize.bat movie.mkv ...       -> command-line mode (same options as normalize.py)
rem
rem Uses Python 3.8+ if the computer has it. Otherwise downloads a private,
rem portable Python into .\runtime\ (nothing is installed system-wide).
setlocal
set "DIR=%~dp0"
set "PY_VERSION=3.13.18"
set "PY_BUILD=20261110"
set "LOCALPY=%DIR%runtime\python\python.exe"

rem The window needs tkinter; command-line mode doesn't.
if "%~1"=="" (
    set "CHECK=import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 8) else 1)"
) else (
    set "CHECK=import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"
)

if exist "%LOCALPY%" (
    set "PY="%LOCALPY%""
    goto run
)
py -3 -c "%CHECK%" >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
    goto run
)
python -c "%CHECK%" >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    goto run
)

rem ---- No suitable Python: download a portable one ------------------------
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" (
    set "TRIPLE=aarch64-pc-windows-msvc"
    set "SHA=f050f4df570616e75aac723a5d05319c0d69ea65243506071bcff162774050ca"
) else (
    set "TRIPLE=x86_64-pc-windows-msvc"
    set "SHA=7e93813bd362c6413f30788b248590e2f169df2d17dcac81aafbcc7a6eeb542c"
)
set "FILE=cpython-%PY_VERSION%+%PY_BUILD%-%TRIPLE%-install_only.tar.gz"
set "URL=https://github.com/astral-sh/python-build-standalone/releases/download/%PY_BUILD%/%FILE%"
set "ARCHIVE=%DIR%runtime\%FILE%"

echo Python 3.8+ not found - downloading a private copy (one time only)...
if not exist "%DIR%runtime" mkdir "%DIR%runtime"
curl -fL --retry 3 --progress-bar -o "%ARCHIVE%" "%URL%"
if errorlevel 1 (
    echo error: could not download Python. Check your internet connection.
    goto fail
)

set "GOT="
for /f "skip=1 tokens=* delims=" %%h in ('certutil -hashfile "%ARCHIVE%" SHA256') do (
    if not defined GOT set "GOT=%%h"
)
set "GOT=%GOT: =%"
if /i not "%GOT%"=="%SHA%" (
    del "%ARCHIVE%"
    echo error: Python download is corrupted ^(checksum mismatch^). Try again.
    goto fail
)

if exist "%DIR%runtime\python" rmdir /s /q "%DIR%runtime\python"
tar -xzf "%ARCHIVE%" -C "%DIR%runtime"
if errorlevel 1 (
    echo error: could not unpack Python.
    goto fail
)
del "%ARCHIVE%"
echo Python installed into %DIR%runtime\python
set "PY="%LOCALPY%""

:run
%PY% "%DIR%normalize.py" %*
set "RC=%errorlevel%"
if not "%RC%"=="0" if "%~1"=="" pause
exit /b %RC%

:fail
pause
exit /b 1
