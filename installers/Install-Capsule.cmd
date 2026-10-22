@echo off
rem Genealogy Capsule installer for Windows: double-click to run. It runs the same one-line install as
rem the README (https://github.com/mjlawless/capsule-genealogy#install-macos-or-windows) in this
rem window, so you can watch what it does. No administrator rights are needed.
rem
rem Windows warns before running a downloaded file that isn't signed. If it says "Windows
rem protected your PC", click "More info", then "Run anyway".
echo Installing Genealogy Capsule...
echo.
powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://raw.githubusercontent.com/mjlawless/capsule-genealogy/main/install.ps1 | iex"
if errorlevel 1 (
  echo.
  echo The install did not finish; the messages above say why.
  echo Help: https://github.com/mjlawless/capsule-genealogy/issues
) else (
  echo.
  echo Done. You can close this window and go back to Chrome.
)
echo.
pause
