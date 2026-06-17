@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Создаём виртуальное окружение, если его нет
if not exist "venv\" (
    echo Создаю виртуальное окружение...
    python -m venv venv
)

REM Активируем и ставим зависимости
call venv\Scripts\activate.bat
pip install -q -r requirements.txt

echo.
echo ================================================================
echo   Запускаю домашнее облако...
echo ================================================================
echo.
python app.py

pause
