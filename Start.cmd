@echo off
setlocal
cd /d "%~dp0"
if not defined MODLINGO_CONDA_ROOT set "MODLINGO_CONDA_ROOT=D:\Anaconda3"
set "MODLINGO_ENV_PATH=%MODLINGO_CONDA_ROOT%\envs\modlingo"
if not exist "%MODLINGO_ENV_PATH%\python.exe" (
    echo Missing Anaconda environment: modlingo. See environment.yml.
    pause
    exit /b 1
)
set "CONDA_EXE=%MODLINGO_CONDA_ROOT%\Scripts\conda.exe"
set "CONDA_PYTHON_EXE=%MODLINGO_CONDA_ROOT%\python.exe"
set "_CE_M="
set "_CE_CONDA="
call "%MODLINGO_CONDA_ROOT%\condabin\conda.bat" activate "%MODLINGO_ENV_PATH%"
if errorlevel 1 exit /b 1
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
set "SSL_CERT_FILE=%MODLINGO_ENV_PATH%\Library\ssl\cacert.pem"
"%MODLINGO_ENV_PATH%\python.exe" -m modlingo %*
set "MODLINGO_EXIT_CODE=%errorlevel%"
if not "%MODLINGO_EXIT_CODE%"=="0" pause
exit /b %MODLINGO_EXIT_CODE%
