@echo off
title vidrepro - Starting...
cd /d "D:\AIProjects\vidrepro"

echo ============================================
echo   vidrepro Dev Server
echo   Frontend: http://localhost:3009
echo   Backend:  http://localhost:3010 (Python)
echo   Note: Requires Docker (postgres, redis, minio)
echo ============================================

echo Checking Docker...
docker info >nul 2>&1
if errorlevel 1 (
    echo Starting Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    echo Waiting for Docker to be ready...
    :docker_loop
    timeout /t 5 /nobreak >nul
    docker info >nul 2>&1
    if errorlevel 1 goto docker_loop
    echo Docker is ready.
) else (
    echo Docker already running.
)

echo Clearing ports 3009 and 3010...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3009 " ^| findstr LISTENING 2^>nul') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3010 " ^| findstr LISTENING 2^>nul') do taskkill /PID %%a /F >nul 2>&1

echo Starting backend services via Docker...
start "vidrepro Backend" cmd /k "cd /d D:\AIProjects\vidrepro && docker compose up -d && echo Backend services started."

echo Waiting for backend to start...
timeout /t 8 /nobreak >nul

echo Starting web frontend...
start "vidrepro Web" cmd /k "cd /d D:\AIProjects\vidrepro\web && npm run dev"

echo Done. vidrepro is starting in new windows.
