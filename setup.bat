@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>nul
title تثبيت خادم المكتبة الشاملة لكلود

rem Work from the script's own folder, quoted: this project's path may contain spaces.
cd /d "%~dp0"

echo ==================================================================
echo    تثبيت خادم المكتبة الشاملة لـ Claude Desktop
echo    Shamela library server for Claude Desktop
echo ==================================================================
echo.
echo    لا يحتاج هذا التثبيت صلاحيات مدير، ولا يعدّل شيئًا من كتبك.
echo.

rem ---- find a usable Python -------------------------------------------------
rem Each candidate is asked to RUN code, not just print a version: that is what
rem rejects the Microsoft Store stub, which exits successfully while doing nothing.
set "PY="
call :try_python py -3
if defined PY goto found
call :try_python python
if defined PY goto found
call :try_python python3
if defined PY goto found
call :try_absolute
if defined PY goto found

rem ---- no Python: offer to install it ---------------------------------------
echo    لم يُعثر على بايثون (Python) على هذا الجهاز، وهو لازم لتشغيل الخادم.
echo.
where winget >nul 2>nul
if errorlevel 1 goto manual_python

echo    يمكن تثبيته تلقائيًّا الآن (يستغرق دقيقة إلى ثلاث دقائق).
echo    اضغط أي زر للبدء، أو أغلق هذه النافذة للإلغاء.
pause >nul
echo.
echo    جارٍ تثبيت بايثون…
winget install -e --id Python.Python.3.12 --scope user --silent ^
    --accept-package-agreements --accept-source-agreements
echo.
echo    اكتمل التثبيت. جارٍ التحقّق…
call :try_python py -3
if defined PY goto found
call :try_python python
if defined PY goto found
call :try_absolute
if defined PY goto found
echo.
echo    ثُبّت بايثون لكن هذه النافذة لا تراه بعد.
echo    أغلق هذه النافذة وانقر على setup.bat مرة أخرى.
echo.
pause
exit /b 1

:manual_python
echo    ستُفتح الآن صفحة تنزيل بايثون.
echo.
echo    خطوات التثبيت:
echo      1) نزّل النسخة المقترحة في أعلى الصفحة.
echo      2) شغّل المثبّت ولا تُغيّر خياراته الافتراضية.
echo      3) ارجع إلى هذه النافذة واضغط أي زر.
echo.
start "" https://www.python.org/downloads/
pause >nul
call :try_python py -3
if defined PY goto found
call :try_python python
if defined PY goto found
call :try_absolute
if defined PY goto found
echo.
echo    ما زال بايثون غير ظاهر. أغلق هذه النافذة وانقر على setup.bat مرة أخرى.
echo.
pause
exit /b 1

:found
echo    بايثون: !PY!
echo.
"!PY!" -X utf8 install.py %*
set "RESULT=!ERRORLEVEL!"

if not "!RESULT!"=="0" (
    echo.
    echo    ==============================================================
    echo       لم يكتمل التثبيت. سيُفتح سجل التثبيت الآن لتراجعه.
    echo    ==============================================================
    if exist "install.log" start "" notepad "install.log"
)

echo.
pause
exit /b !RESULT!

rem ---- helpers --------------------------------------------------------------

:try_python
rem %* is a candidate launcher. Runs real code to confirm it works and is >= 3.10.
%* -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%*"
exit /b 0

:try_absolute
rem After installing Python in this session, PATH is stale in the current console.
rem Probing the known install locations directly is what makes "retry now" work.
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
    if exist %%D (
        %%D -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
        if not errorlevel 1 (
            set "PY=%%~D"
            exit /b 0
        )
    )
)
exit /b 0
