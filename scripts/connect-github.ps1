$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskEnvPath = Join-Path $taskRoot '.env'
Write-Output 'Use a separate fine-grained GitHub token: selected repositories; Contents READ, Metadata READ.'
Write-Output 'This script only saves local settings. It makes no API calls.'
$taskSecureKey = Read-Host 'GitHub fine-grained token (hidden)' -AsSecureString
$taskBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($taskSecureKey)
try {
    $taskKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($taskBstr).Trim()
    if ($taskKey -notmatch '^github_pat_[A-Za-z0-9_]{20,}$') { throw 'Expected a fine-grained token. Nothing saved.' }
    $taskText = if (Test-Path -LiteralPath $taskEnvPath) { [IO.File]::ReadAllText($taskEnvPath) } else { [IO.File]::ReadAllText((Join-Path $taskRoot '.env.example')) }
    $taskLines = @($taskText -split '\r?\n' | Where-Object { $_ -notmatch '^\s*KOTO_GITHUB_TOKEN\s*=' })
    $taskLines += "KOTO_GITHUB_TOKEN=$taskKey"
    [IO.File]::WriteAllText($taskEnvPath, (($taskLines -join "`n").TrimEnd() + "`n"), [Text.UTF8Encoding]::new($false))
    Write-Output 'GitHub token saved locally. No API requests made.'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($taskBstr)
    $taskKey = $null
    $taskLines = $null
    $taskSecureKey.Dispose()
}
