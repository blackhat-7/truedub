@echo off
rem Runs the TrueDub companion in this window. Close it to stop.
cd /d "%~dp0"
uv run truedub %*
