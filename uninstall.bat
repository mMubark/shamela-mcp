@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>nul
title Shamela for Claude - Uninstall

cd /d "%~dp0"

set "RC=1"
set "PY_EXE="
set "PY_ARG="

echo ==================================================================
echo    Removing the Shamela server from Claude Desktop settings
echo ==================================================================
echo.
echo    Your Shamela library and your books are not touched.
echo    Claude's settings file is backed up before any change.
echo.
echo    Press any key to continue, or close this window to cancel.
pause >nul
echo.

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0.venv\Scripts\python.exe"
    goto ready
)
call :probe "py" "-3"
if defined PY_EXE goto ready
call :probe "python" ""
if defined PY_EXE goto ready

echo    Python was not found, so the entry cannot be removed automatically.
echo    You can delete the "shamela" entry by hand from this file:
echo      %%APPDATA%%\Claude\claude_desktop_config.json
goto done

:ready
"%PY_EXE%" %PY_ARG% -X utf8 "%~dp0install.py" --uninstall
set "RC=%ERRORLEVEL%"
goto done

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
