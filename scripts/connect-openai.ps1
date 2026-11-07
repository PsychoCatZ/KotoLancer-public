$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskEnvPath = Join-Path $taskRoot '.env'
Write-Output 'Enter an OpenAI API key. It will be saved only in the ignored local .env file.'
Write-Output 'This script makes no API calls, selects no model and starts no paid request.'
$taskSecureKey = Read-Host 'OpenAI API key (hidden)' -AsSecureString
$taskBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($taskSecureKey)
try {
    $taskKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($taskBstr).Trim()
    if ($taskKey.Length -lt 20 -or $taskKey -match '[\r\n]') { throw 'Key is empty or invalid. Nothing saved.' }
    $taskText = if (Test-Path -LiteralPath $taskEnvPath) { [IO.File]::ReadAllText($taskEnvPath) } else { [IO.File]::ReadAllText((Join-Path $taskRoot '.env.example')) }
    $taskLines = @($taskText -split '\r?\n' | Where-Object { $_ -notmatch '^\s*KOTO_OPENAI_API_KEY\s*=' })
    $taskLines += "KOTO_OPENAI_API_KEY=$taskKey"
    [IO.File]::WriteAllText($taskEnvPath, (($taskLines -join "`n").TrimEnd() + "`n"), [Text.UTF8Encoding]::new($false))
    Write-Output 'Key saved locally. No API requests made. Model and live-check limit will be configured separately.'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($taskBstr)
    $taskKey = $null
    $taskLines = $null
    $taskSecureKey.Dispose()
}
