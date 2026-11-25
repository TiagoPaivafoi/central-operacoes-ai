@echo off
cd /d "%~dp0"
title Central de Operacoes AI
set "PY=python"
%PY% --version
if errorlevel 1 set "PY=py"
echo.
echo === Passo 1 de 2: preparando as bibliotecas (na primeira vez demora alguns minutos) ===
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto erro
echo.
echo === Passo 2 de 2: abrindo o painel no navegador ===
echo NAO FECHE ESTA JANELA enquanto estiver usando o painel.
echo.
where tailscale >nul 2>nul
if errorlevel 1 goto semtailscale
echo Liberando o acesso pelo celular (Tailscale)...
tailscale serve --bg --http=80 8502 >nul 2>nul
echo No celular, com o Tailscale ligado, abra: http://criador-de-campanha
echo.
:semtailscale
%PY% -m streamlit run app.py
goto fim
:erro
echo.
echo ERRO ao preparar as bibliotecas. Tire um print desta tela e envie ao Claude.
:fim
echo.
pause
