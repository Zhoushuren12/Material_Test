@echo off
setlocal

cd /d "%~dp0"

python -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo PySide6 is not installed. Installing project dependencies...
    python -m pip install -e .
    if errorlevel 1 (
        echo.
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
)

python -m material_test.app
if errorlevel 1 (
    echo.
    echo Failed to start Material Test.
    pause
    exit /b 1
)

endlocal
