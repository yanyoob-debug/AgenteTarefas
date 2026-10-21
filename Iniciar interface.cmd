@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Crie o ambiente virtual e instale requirements.txt seguindo o README.
    pause
    exit /b 1
)
set PYTHONUTF8=1
echo.
echo Abra http://127.0.0.1:8765 no navegador.
echo Para encerrar, pressione Ctrl+C nesta janela.
echo.
".venv\Scripts\python.exe" -m uvicorn webapp:app --host 127.0.0.1 --port 8765
pause
