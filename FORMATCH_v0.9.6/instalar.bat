@echo off
setlocal
cd /d "%~dp0"
py -3.11 -m venv .venv
if errorlevel 1 goto :erro
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -e .
if errorlevel 1 goto :erro
echo.
echo Instalacao concluida.
pause
exit /b 0
:erro
echo.
echo Nao foi possivel concluir a instalacao.
pause
exit /b 1

