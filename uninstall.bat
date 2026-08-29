@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>nul
title إزالة خادم المكتبة الشاملة من كلود

cd /d "%~dp0"

echo ==================================================================
echo    إزالة خادم المكتبة الشاملة من إعدادات Claude Desktop
echo ==================================================================
echo.
echo    لن يُمسّ أي شيء من مكتبتك الشاملة ولا من كتبك.
echo    ستُؤخذ نسخة احتياطية من إعدادات كلود قبل التغيير.
echo.
echo    اضغط أي زر للمتابعة، أو أغلق هذه النافذة للإلغاء.
pause >nul
echo.

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    python -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo    لم يُعثر على بايثون، ولا يمكن إتمام الإزالة تلقائيًّا.
    echo    يمكنك حذف مدخل "shamela" يدويًّا من الملف:
    echo      %%APPDATA%%\Claude\claude_desktop_config.json
    echo.
    pause
    exit /b 1
)

"!PY!" -X utf8 install.py --uninstall
set "RESULT=!ERRORLEVEL!"

echo.
pause
exit /b !RESULT!
