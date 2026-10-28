@echo off
pip install -q PySide6-Essentials || exit /b 1
python "%~dp0kernel.py"
