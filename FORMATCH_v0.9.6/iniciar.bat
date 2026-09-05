@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Ambiente do FORMATCH nao encontrado. Iniciando recuperacao...
  call instalar.bat
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -m formatura_distribuidor
