$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskEnvPath = Join-Path $taskRoot '.env'
Write-Output 'Use the token of the dedicated KotoLancer bot. For rotation, renew the EXISTING bot token in @BotFather; do not create another bot.'
Write-Output 'This script only saves local settings. It makes no API calls and starts no bot.'
$taskSecureKey = Read-Host 'Telegram bot token (hidden)' -AsSecureString
$taskBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($taskSecureKey)
try {
    $taskKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($taskBstr).Trim()
    if ($taskKey -notmatch '^\d+:[A-Za-z0-9_-]{20,}$') { throw 'Invalid token format. Nothing saved.' }
    $taskOwner = (Read-Host 'Your numeric Telegram user ID (Enter if unknown)').Trim()
    if ($taskOwner -and $taskOwner -notmatch '^[1-9]\d{0,18}$') { throw 'User ID must contain digits. Nothing saved.' }
    $taskText = if (Test-Path -LiteralPath $taskEnvPath) { [IO.File]::ReadAllText($taskEnvPath) } else { [IO.File]::ReadAllText((Join-Path $taskRoot '.env.example')) }
    $taskLines = @($taskText -split '\r?\n' | Where-Object { $_ -notmatch '^\s*KOTO_TELEGRAM_BOT_TOKEN\s*=' -and (-not $taskOwner -or $_ -notmatch '^\s*KOTO_TELEGRAM_OWNER_ID\s*=') })
    $taskLines += "KOTO_TELEGRAM_BOT_TOKEN=$taskKey"
    if ($taskOwner) { $taskLines += "KOTO_TELEGRAM_OWNER_ID=$taskOwner" }
    [IO.File]::WriteAllText($taskEnvPath, (($taskLines -join "`n").TrimEnd() + "`n"), [Text.UTF8Encoding]::new($false))
    Write-Output 'Telegram settings saved locally. No network calls made.'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($taskBstr)
    $taskKey = $null
    $taskLines = $null
    $taskSecureKey.Dispose()
}
