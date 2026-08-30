@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>nul
title Shamela for Claude - Setup

cd /d "%~dp0"

set "RC=1"
set "PY_EXE="
set "PY_ARG="

echo ==================================================================
echo    Shamela library server for Claude Desktop  --  installer
echo    (Arabic instructions start once Python is found)
echo ==================================================================
echo.
echo    No administrator rights are needed, and nothing in your
echo    Shamela library or your books is modified.
echo.

call :probe "py" "-3"
if defined PY_EXE goto ready
call :probe "python" ""
if defined PY_EXE goto ready
call :probe "python3" ""
if defined PY_EXE goto ready
call :probe_known_paths
if defined PY_EXE goto ready
goto no_python

:ready
echo    Python: "%PY_EXE%" %PY_ARG%
echo.
"%PY_EXE%" %PY_ARG% -X utf8 "%~dp0install.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo    ---------------------------------------------------------------
    echo    Setup did not finish. Opening install.log with the details.
    echo    ---------------------------------------------------------------
    if exist "%~dp0install.log" start "" notepad "%~dp0install.log"
)
goto done

:no_python
if exist "%~dp0assets\python-missing-ar.txt" type "%~dp0assets\python-missing-ar.txt"
echo.
echo    Python 3.10 or newer was not found on this computer.
echo.
where winget >nul 2>nul
if errorlevel 1 goto manual_python

echo    Press any key to install Python automatically (1-3 minutes),
echo    or close this window to cancel.
pause >nul
echo.
echo    Installing Python, please wait...
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
echo.
echo    Checking again...
call :probe "py" "-3"
if defined PY_EXE goto ready
call :probe "python" ""
if defined PY_EXE goto ready
call :probe_known_paths
if defined PY_EXE goto ready
if exist "%~dp0assets\python-installed-ar.txt" type "%~dp0assets\python-installed-ar.txt"
echo    Python was installed, but this window cannot see it yet.
echo    Close this window and double-click setup.bat again.
goto done

:manual_python
echo    The Python download page will open now.
echo      1) Download the version offered at the top of the page.
echo      2) Run the installer and keep its default options.
echo      3) Come back to this window and press any key.
echo.
start "" https://www.python.org/downloads/
pause >nul
call :probe "py" "-3"
if defined PY_EXE goto ready
call :probe "python" ""
if defined PY_EXE goto ready
call :probe_known_paths
if defined PY_EXE goto ready
if exist "%~dp0assets\python-installed-ar.txt" type "%~dp0assets\python-installed-ar.txt"
echo    Python is still not visible to this window.
echo    Close this window and double-click setup.bat again.
goto done

:done
echo.
echo    Press any key to close this window.
pause >nul
endlocal & exit /b %RC%

rem ======================== helpers ========================

:probe
rem %~1 launcher, %~2 extra argument. The candidate must RUN code, not just print a
rem version: that is what rejects the Microsoft Store stub, which exits successfully
rem while doing nothing at all.
set "_exe=%~1"
set "_arg=%~2"
%_exe% %_arg% -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY_EXE=%_exe%"
set "PY_ARG=%_arg%"
exit /b 0

:probe_known_paths
rem Right after installing Python, PATH is stale in this already-open window, so the
rem usual install locations are probed by absolute path instead.
for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python311\python.exe"
    "%ProgramFiles%\Python310\python.exe"
) do (
    if exist "%%~D" (
        "%%~D" -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
        if not errorlevel 1 (
            set "PY_EXE=%%~D"
            set "PY_ARG="
            exit /b 0
        )
    )
)
exit /b 0
