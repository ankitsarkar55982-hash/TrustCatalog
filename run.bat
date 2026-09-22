@echo off
REM ====================================================================
REM  TrustCatalog - one-command launcher for Windows
REM  Double-click this file, or run it from the VS Code terminal.
REM ====================================================================
setlocal
cd /d "%~dp0"

echo.
echo ==========================================================
echo   TRUSTCATALOG - AI E-Commerce Integrity Monitor
echo ==========================================================
echo.

REM ---- 1. find Python -------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found on your PATH.
    echo         Install Python 3.11+ from python.org and tick
    echo         "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)"
if errorlevel 1 (
    echo [ERROR] Python 3.9 or newer is required.
    python --version
    pause
    exit /b 1
)

REM ---- 2. virtual environment ----------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [1/6] Creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [1/6] Virtual environment already exists.
)

set "PY=.venv\Scripts\python.exe"

REM ---- 3. dependencies ------------------------------------------------
"%PY%" -c "import streamlit, plotly, sklearn, pandas" >nul 2>&1
if errorlevel 1 (
    echo [2/6] Installing dependencies ^(first run only, takes a few minutes^) ...
    "%PY%" -m pip install --upgrade pip
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed. Check your internet connection.
        pause
        exit /b 1
    )
) else (
    echo [2/6] Dependencies already installed.
)

REM ---- 4. data --------------------------------------------------------
if exist "data\raw\olist_orders_dataset.csv" (
    echo [3/6] Real Olist dataset found in data\raw - using it.
) else (
    if exist "data\demo\olist_orders_dataset.csv" (
        echo [3/6] Demo dataset already generated.
    ) else (
        echo [3/6] No dataset found - generating DEMO data ...
        "%PY%" scripts\generate_demo_data.py
        if errorlevel 1 goto :failed
    )
)

if exist "database\trustcatalog.db" (
    echo [4/6] Pipeline results already exist. Delete database\trustcatalog.db to force a rerun.
) else (
    echo [4/6] Running the risk pipeline ...
    "%PY%" scripts\run_pipeline.py
    if errorlevel 1 goto :failed
)

REM ---- 6. shop catalog + demo accounts --------------------------------
echo [5/6] Seeding shop accounts and product catalog ...
"%PY%" scripts\init_catalog.py
if errorlevel 1 goto :failed

REM ---- 7. launch ------------------------------------------------------
echo [6/6] Launching the platform - your browser will open shortly.
echo       Demo accounts: demo_customer / demo_seller / demo_admin
echo       Password for all three: DemoPass!2026  (DEMO ONLY)
echo       Press Ctrl+C in this window to stop the server.
echo.
"%PY%" -m streamlit run dashboard\app.py
goto :eof

:failed
echo.
echo [ERROR] A step failed. Scroll up for the traceback.
pause
exit /b 1
