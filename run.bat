@echo off
chcp 65001 >nul
title 划词翻译

:: Check if dependencies are installed
pip show pyperclip >nul 2>&1
if errorlevel 1 (
    echo [安装依赖...]
    pip install -r "%~dp0requirements.txt"
    echo.
)

:: Launch the translator
python "%~dp0translate.py"
pause
