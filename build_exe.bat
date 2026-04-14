@echo off
echo ============================================
echo   FloatTask - Building EXE
echo ============================================
echo.
echo Installing PyInstaller (agar nahi hai)...
pip install pyinstaller
echo.
echo Building FloatTask.exe...
pyinstaller --onefile --windowed --name="FloatTask" floating_tasks.py
echo.
echo Done! EXE yahan milega: dist\FloatTask.exe
pause
