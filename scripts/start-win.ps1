# Запуск воркера repetit-agent на Windows (один аккаунт).
# Chrome воркер поднимет сам (профиль data\chrome-profiles\main, CDP :9335);
# если нет сессии — залогинься в repetit.ru в этом Chrome, воркер подхватит.
# Лог воркера: logs\worker.log, консоль: logs\console.log. Стоп: scripts\stop-win.ps1
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$runScript = @"
Set-Location "$repo"
`$env:PYTHONUTF8 = "1"
`$env:PYTHONPATH = "$repo\src"
`$env:REPETIT_CHROME_PATH = "C:\Program Files\Google\Chrome\Application\chrome.exe"
& "$repo\.venv\Scripts\python.exe" -m repetit run *>> "$repo\logs\console.log"
"@
$runPs = Join-Path $env:TEMP "repetit-worker-win.ps1"
$runScript | Out-File -FilePath $runPs -Encoding utf8
Start-Process -FilePath "powershell" -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $runPs `
  -WindowStyle Hidden -WorkingDirectory $repo

Write-Host "стартовано: воркер repetit (логи: logs\worker.log, logs\console.log)"
