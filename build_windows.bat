@echo off
REM SchoolLive Player – Windows .exe build
REM Futtatás: build_windows.bat

pip install -r requirements.txt

pyinstaller ^
    --onefile ^
    --windowed ^
    --name SchoolLivePlayer ^
    --icon schoollive.ico ^
    --add-data "schoollive.ico;." ^
    main.py

echo.
echo Build kész: dist\SchoolLivePlayer.exe
