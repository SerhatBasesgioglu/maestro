"""Trusted, constant PowerShell source. Caller input is ALWAYS bound as parameters."""

APPLICATION_POOL = r"""
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][ValidateSet('Get', 'Start', 'Stop')][string]$Operation
)
$ErrorActionPreference = 'Stop'
Import-Module WebAdministration -ErrorAction Stop

# Literal lookup distinguishes a missing pool from a missing cmdlet/module.
# The Python API rejects path separators and wildcard characters in Name.
try {
    $null = Get-Item -LiteralPath ('IIS:\AppPools\' + $Name) -ErrorAction Stop
} catch {
    if ($_.CategoryInfo.Category -eq 'ObjectNotFound') {
        Write-Error -Message 'The requested application pool does not exist.' `
            -ErrorId 'Conductor.ApplicationPoolNotFound' -Category ObjectNotFound -ErrorAction Stop
    }
    throw
}

$state = [string](Get-WebAppPoolState -Name $Name -ErrorAction Stop).Value
$changed = $false
# Recheck remotely, and never issue a command during a transition.
if ($Operation -eq 'Start' -and $state -eq 'Stopped') {
    $null = Start-WebAppPool -Name $Name -ErrorAction Stop
    $changed = $true
} elseif ($Operation -eq 'Stop' -and $state -eq 'Started') {
    $null = Stop-WebAppPool -Name $Name -ErrorAction Stop
    $changed = $true
}
if ($changed) {
    $state = [string](Get-WebAppPoolState -Name $Name -ErrorAction Stop).Value
}
@{ state = $state; changed = $changed } | ConvertTo-Json -Compress
"""
