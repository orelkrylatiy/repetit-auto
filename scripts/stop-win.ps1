# Остановить воркер repetit-agent на Windows (Chrome не трогаем — сессия живёт в профиле).
$repo = Split-Path -Parent $PSScriptRoot
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='powershell.exe'" |
  Where-Object { $_.CommandLine -match "repetit-worker-win|-m repetit" } |
  ForEach-Object {
    Write-Host "kill $($_.ProcessId): $($_.CommandLine.Substring(0, [Math]::Min(90, $_.CommandLine.Length)))"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Write-Host "остановлено"
