# Запуск ВТОРОГО repetit-воркера (акк «инфа» в браузере profi3, CDP :9224).
# Отличается от start-win.ps1 только env: свой порт, БД, лог и тег.
# Chrome поднимает супервизор profi3 (profi-agent scripts\start-win.ps1 -Account profi3).
# Стоп: scripts\stop-win.ps1 (гасит оба repetit-воркера).
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$runScript = @"
Set-Location "$repo"
`$env:PYTHONUTF8 = "1"
`$env:PYTHONPATH = "$repo\src"
`$env:REPETIT_CHROME_PATH = "C:\Program Files\Google\Chrome\Application\chrome.exe"
`$env:REPETIT_CHROME_NO_LAUNCH = "1"
`$env:REPETIT_CDP_PORT = "9224"
`$env:REPETIT_DB = "$repo\data\info.db"
`$env:REPETIT_LOG_TAG = "info"
# фильтры те же, что у profi-инфы: только информатика (ОГЭ/ЕГЭ), стопы c++/олимпиад
`$env:REPETIT_SUBJECTS = "информатик,программирован"
`$env:REPETIT_STOP_PATTERNS = "c++,с++,олимпиад"
& "$repo\.venv\Scripts\python.exe" -m repetit run *>> "$repo\logs\console-info.log"
"@
$runPs = Join-Path $env:TEMP "repetit-worker-info-win.ps1"
$runScript | Out-File -FilePath $runPs -Encoding utf8
Start-Process -FilePath "powershell" -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $runPs `
  -WindowStyle Hidden -WorkingDirectory $repo

Write-Host "стартовано: repetit info (CDP 9224, логи: logs\worker-info.log, logs\console-info.log)"
