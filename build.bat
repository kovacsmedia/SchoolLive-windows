@echo off
REM SchoolLive Windows Player – teljes build
REM Szükséges: Python 3.11+, PyInstaller, Inno Setup 6

setlocal
set DIST=dist

echo.
echo ========================================
echo  1/3  SchoolLiveUpdater.exe build
echo ========================================
pip install pyinstaller --quiet

pyinstaller ^
    --onefile ^
    --windowed ^
    --name SchoolLiveUpdater ^
    --distpath %DIST% ^
    updater\updater.py

if errorlevel 1 (
    echo [HIBA] Updater build sikertelen
    exit /b 1
)

echo.
echo ========================================
echo  2/3  SchoolLivePlayer.exe build
echo ========================================
pip install -r player\requirements.txt --quiet

pyinstaller ^
    --onefile ^
    --windowed ^
    --name SchoolLivePlayer ^
    --distpath %DIST% ^
    --add-data "player\schoollive.ico;." ^
    --icon player\schoollive.ico ^
    --paths player ^
    player\main.py

if errorlevel 1 (
    echo [HIBA] Player build sikertelen
    exit /b 1
)

echo.
echo ========================================
echo  3/3  Installer build (Inno Setup)
echo ========================================
REM Inno Setup compiler keresése
set ISCC=""
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe"       set ISCC="C:\Program Files\Inno Setup 6\ISCC.exe"

if %ISCC%=="" (
    echo [FIGYELEM] Inno Setup nem talalhato – installer kimarad
    echo Telepitsd: https://jrsoftware.org/isinfo.php
) else (
    mkdir dist\installer 2>nul
    %ISCC% installer\setup.iss
    if errorlevel 1 (
        echo [HIBA] Installer build sikertelen
    ) else (
        echo [OK] Installer kesz: dist\installer\
    )
)

echo.
echo ========================================
echo  Build kesz!
echo   dist\SchoolLivePlayer.exe
echo   dist\SchoolLiveUpdater.exe
echo   dist\installer\SchoolLivePlayer_Setup_*.exe
echo ========================================
