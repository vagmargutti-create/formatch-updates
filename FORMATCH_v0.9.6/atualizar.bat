@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Ambiente anterior nao encontrado. Execute instalar.bat.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install -e .
if errorlevel 1 (
  echo Nao foi possivel atualizar.
  pause
  exit /b 1
)
echo Atualizacao concluida.
pause
