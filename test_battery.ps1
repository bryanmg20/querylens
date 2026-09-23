$ErrorActionPreference = "Stop"
$Root = "C:\Users\bryan\OneDrive\Escritorio\querylens"
$Py = Join-Path $Root "telemetry_pipeline\venv\Scripts\python.exe"

Write-Host "~~~ 1/3 Bateria (10 queries x 30 reps, limpia logs + stats) ~~~"
docker exec ql_sysbench bash /scripts/battery.sh 30

Write-Host "~~~ 2/3 Pipeline (encola 1 mensaje por motor) ~~~"
& $Py (Join-Path $Root "telemetry_pipeline\main.py")

Write-Host "~~~ 3/3 Resultados por motor ~~~"
& $Py (Join-Path $Root "telemetry_pipeline\compare_queue.py") --limit 2