@echo off
REM ===================================================================
REM  One-click build for RetailPOS.
REM  Double-click this file, or run it from a terminal:
REM      build.bat                 full build + verify + vc_redist fetch
REM      build.bat --skip-redist   skip the vc_redist download
REM ===================================================================
setlocal
cd /d "%~dp0"

REM Prefer the project virtual environment; fall back to system Python.
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [build.bat] Using Python: %PY%
"%PY%" build_exe.py %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [build.bat] BUILD FAILED with exit code %RC%.
) else (
    echo [build.bat] BUILD OK.  Portable app: dist\RetailPOS\
    echo [build.bat] Installer : compile installer.iss with Inno Setup 6.
)

echo.
pause
exit /b %RC%
