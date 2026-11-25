@echo off
cd /d "%~dp0"
title Configurar o painel
set "PY=python"
%PY% --version
if errorlevel 1 set "PY=py"
%PY% configurar.py
if errorlevel 1 (
  echo.
  echo Algo deu errado. Tire um print desta tela e envie ao Claude.
  pause
)
