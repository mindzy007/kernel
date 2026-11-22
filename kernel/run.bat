@echo off
set PYTHONDONTWRITEBYTECODE=1
pip install -q PySide6-Essentials || exit /b 1
python -B "%~dp0kernel.py"
