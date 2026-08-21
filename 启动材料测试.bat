@echo off
setlocal

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Please install Python 3.11 or later and add it to PATH.
    pause
    exit /b 1
)

python -c "import PySide6, matplotlib, numpy, openpyxl, pandas, pyqtgraph, scipy, yaml" >nul 2>nul
if errorlevel 1 (
    echo Installing Material Test dependencies...
    python -m pip install -e "%~dp0"
    if errorlevel 1 (
        echo.
        echo Failed to install Material Test dependencies.
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
