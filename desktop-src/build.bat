@echo off
chcp 65001 >nul
echo 正在使用 PyInstaller 重新打包 ImageGrabber.exe ...
pyinstaller --noconfirm --onedir --windowed --name ImageGrabber --icon ..\app.ico launcher.py
echo 打包完成！输出位于 dist\ImageGrabber\
pause
