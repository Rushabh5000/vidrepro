@echo off
echo Stopping vidrepro (ports 3009 and 3010)...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3009 " ^| findstr LISTENING 2^>nul') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3010 " ^| findstr LISTENING 2^>nul') do taskkill /PID %%a /F >nul 2>&1
cd /d "D:\AIProjects\vidrepro" && docker compose down >nul 2>&1
echo vidrepro stopped.
