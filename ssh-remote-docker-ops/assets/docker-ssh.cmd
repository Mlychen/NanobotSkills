@echo off
setlocal

set "DOCKER_SSH_HOME=%USERPROFILE%\docker-ssh"
set "DOCKER_SOURCE=C:\Program Files\Docker\Docker\resources\bin\docker.exe"
set "DOCKER_DEST=%DOCKER_SSH_HOME%\docker.exe"
set "CONTEXT_SOURCE=%USERPROFILE%\.docker\contexts"
set "CONTEXT_DEST=%DOCKER_SSH_HOME%\contexts"
set "CONFIG_PATH=%DOCKER_SSH_HOME%\config.json"

if not exist "%DOCKER_SSH_HOME%" mkdir "%DOCKER_SSH_HOME%"

if not exist "%DOCKER_SOURCE%" (
  echo docker.exe not found at "%DOCKER_SOURCE%".
  endlocal & exit /b 1
)

if not exist "%DOCKER_DEST%" (
  copy /Y "%DOCKER_SOURCE%" "%DOCKER_DEST%" >nul
)

if not exist "%CONFIG_PATH%" (
  >"%CONFIG_PATH%" echo {"auths":{},"currentContext":"desktop-linux"}
)

if exist "%CONTEXT_SOURCE%" (
  if not exist "%CONTEXT_DEST%" mkdir "%CONTEXT_DEST%"
  xcopy /E /I /Y "%CONTEXT_SOURCE%" "%CONTEXT_DEST%" >nul
)

set "DOCKER_CONFIG=%DOCKER_SSH_HOME%"
set "PATH=C:\Windows\System32;C:\Windows;C:\Windows\System32\WindowsPowerShell\v1.0"

"%DOCKER_DEST%" %*
set "EXITCODE=%ERRORLEVEL%"
endlocal & exit /b %EXITCODE%
