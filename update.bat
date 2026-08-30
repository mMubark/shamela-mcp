@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>nul
title Shamela for Claude - Update

cd /d "%~dp0"

set "RC=1"
set "PY_EXE="
set "PY_ARG="

echo ==================================================================
echo    Shamela library server for Claude Desktop  --  update
echo ==================================================================
echo.
echo    Your Shamela library, your books, and Claude's settings entry
echo    are left as they are. Only this program's own files change.
echo.

rem The environment created by setup.bat is the one Claude actually runs, so it is
rem the right interpreter to update with; a system Python would install elsewhere.
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0.venv\Scripts\python.exe"
    goto ready
)
call :probe "py" "-3"
if defined PY_EXE goto ready
call :probe "python" ""
if defined PY_EXE goto ready
call :probe "python3" ""
if defined PY_EXE goto ready

echo    Python was not found, and this folder has no environment yet.
echo    Run setup.bat first; it installs everything from scratch.
goto done

:ready
echo    Python: "%PY_EXE%" %PY_ARG%
echo.
"%PY_EXE%" %PY_ARG% -X utf8 "%~dp0install.py" --update %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo    ---------------------------------------------------------------
    echo    The update did not finish. Opening install.log with the details.
    echo    ---------------------------------------------------------------
    if exist "%~dp0install.log" start "" notepad "%~dp0install.log"
)

:done
echo.
echo    Press any key to close this window.
pause >nul
endlocal & exit /b %RC%

rem ======================== helpers ========================

:probe
set "_exe=%~1"
set "_arg=%~2"
%_exe% %_arg% -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY_EXE=%_exe%"
set "PY_ARG=%_arg%"
exit /b 0
