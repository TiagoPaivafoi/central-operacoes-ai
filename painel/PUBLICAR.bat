@echo off
cd /d "%~dp0"
title Robo de Publicacao - Central de Operacoes AI
set "PY=python"
%PY% --version
if errorlevel 1 set "PY=py"
echo.
echo Este robo publica sozinho os posts AGENDADOS quando chega a hora.
echo Deixe esta janela aberta (pode minimizar). Para parar, feche a janela.
echo.
%PY% worker.py
echo.
pause
