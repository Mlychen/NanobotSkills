[CmdletBinding()]
param(
    [Alias("Host")]
    [string]$TargetHost = "winnas",
    [string]$RemoteHome,
    [switch]$IncludePullChecks,
    [string]$Image
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-SshCapture {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HostName,
        [Parameter(Mandatory = $true)]
        [string]$Command
    )

    $output = & ssh $HostName $Command 2>&1
    if ($LASTEXITCODE -ne 0) {
        $text = ($output -join "`n").Trim()
        throw "SSH command failed: $Command`n$text"
    }

    return $output
}

function Format-WrapperCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WrapperPath,
        [Parameter(Mandatory = $true)]
        [string]$Arguments
    )

    return ('cmd /c ""{0}" {1}"' -f $WrapperPath, $Arguments)
}

if (-not $RemoteHome) {
    $RemoteHome = Invoke-SshCapture -HostName $TargetHost -Command "cmd /c echo %USERPROFILE%"
    $RemoteHome = (($RemoteHome -join "`n").Trim()).Trim('"')

    if (-not $RemoteHome) {
        throw "Unable to determine %USERPROFILE% on $TargetHost."
    }
}

$wrapperPath = Join-Path $RemoteHome "docker-ssh.cmd"

$coreChecks = @(
    @{
        Name = "docker version"
        Command = Format-WrapperCommand -WrapperPath $wrapperPath -Arguments "version"
    },
    @{
        Name = "docker compose version"
        Command = Format-WrapperCommand -WrapperPath $wrapperPath -Arguments "compose version"
    }
)

function Invoke-CheckGroup {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,
        [Parameter(Mandatory = $true)]
        [object[]]$Checks
    )

    Write-Host ""
    Write-Host ("== {0} ==" -f $Title)

    foreach ($check in $Checks) {
        Write-Host ""
        Write-Host ("==> {0}" -f $check.Name)
        $output = Invoke-SshCapture -HostName $TargetHost -Command $check.Command
        if ($output) {
            $output | ForEach-Object { Write-Host $_ }
        }
    }
}

Write-Host "Using remote wrapper: $wrapperPath"
Invoke-CheckGroup -Title "Core wrapper / CLI checks" -Checks $coreChecks

if ($IncludePullChecks) {
    $pullChecks = @(
        @{
            Name = "docker pull hello-world"
            Command = Format-WrapperCommand -WrapperPath $wrapperPath -Arguments "pull hello-world"
        }
    )

    if ($Image) {
        $pullChecks += @(
            @{
                Name = "docker pull $Image"
                Command = Format-WrapperCommand -WrapperPath $wrapperPath -Arguments ("pull {0}" -f $Image)
            },
            @{
                Name = "docker image inspect $Image"
                Command = Format-WrapperCommand -WrapperPath $wrapperPath -Arguments ('image inspect --format "{{{{.Id}}}}" {0}' -f $Image)
            }
        )
    }

    Invoke-CheckGroup -Title "Optional registry / image checks" -Checks $pullChecks
}
else {
    Write-Host ""
    Write-Host "== Optional registry / image checks skipped =="
    Write-Host "Run again with -IncludePullChecks [-Image <image>] after docker login or when registry access is available."
}

Write-Host ""
Write-Host "Remote wrapper / CLI verification passed."
