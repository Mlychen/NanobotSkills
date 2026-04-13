[CmdletBinding()]
param(
    [Alias("Host")]
    [string]$TargetHost = "winnas",
    [string]$RemoteUser = "WinNas",
    [string]$RemoteHome = "C:\Users\WinNas"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Convert-ToScpPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WindowsPath
    )

    if ($WindowsPath -notmatch "^[A-Za-z]:\\") {
        throw "Remote path must be an absolute Windows path: $WindowsPath"
    }

    $drive = $WindowsPath.Substring(0, 1)
    $rest = $WindowsPath.Substring(2).Replace("\", "/")
    return "/${drive}:$rest"
}

function Invoke-SshChecked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HostName,
        [Parameter(Mandatory = $true)]
        [string]$Command
    )

    & ssh $HostName $Command
    if ($LASTEXITCODE -ne 0) {
        throw "SSH command failed: $Command"
    }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$skillRoot = Split-Path -Parent $scriptDir
$localWrapperPath = Join-Path $skillRoot "assets\docker-ssh.cmd"

if (-not (Test-Path -LiteralPath $localWrapperPath)) {
    throw "Wrapper template not found: $localWrapperPath"
}

$remoteWrapperPath = Join-Path $RemoteHome "docker-ssh.cmd"
$remoteScpPath = Convert-ToScpPath -WindowsPath $remoteWrapperPath

Write-Host "Checking SSH identity on $TargetHost ..."
$actualUser = (& ssh $TargetHost "cmd /c echo %USERNAME%")
if ($LASTEXITCODE -ne 0) {
    throw "Unable to query remote username on $TargetHost."
}

$actualUser = (($actualUser -join "`n").Trim()).Trim('"')
if ($actualUser -and $actualUser -ne $RemoteUser) {
    Write-Warning "Connected as '$actualUser', expected '$RemoteUser'. Continuing because RemoteHome was provided explicitly."
}

Write-Host "Ensuring remote home exists: $RemoteHome"
Invoke-SshChecked -HostName $TargetHost -Command ('cmd /c if not exist "{0}" mkdir "{0}"' -f $RemoteHome)

Write-Host "Copying wrapper to $remoteWrapperPath"
& scp $localWrapperPath ("{0}:{1}" -f $TargetHost, $remoteScpPath)
if ($LASTEXITCODE -ne 0) {
    throw "scp failed while uploading $localWrapperPath to $remoteWrapperPath"
}

Write-Host "Verifying remote wrapper ..."
Invoke-SshChecked -HostName $TargetHost -Command ('powershell -NoProfile -Command "if (Test-Path -LiteralPath ''{0}'') {{ Write-Output ''INSTALLED {0}'' }} else {{ exit 1 }}"' -f $remoteWrapperPath)

Write-Host "Remote wrapper installed successfully."
Write-Host "Next step:"
Write-Host ('powershell -ExecutionPolicy Bypass -File "{0}" -Host {1} -RemoteHome "{2}"' -f (Join-Path $scriptDir "verify-remote-wrapper.ps1"), $TargetHost, $RemoteHome)
