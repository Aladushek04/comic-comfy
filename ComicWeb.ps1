# comic-comfy: local web UI. Ctrl+C stops the server without cmd's batch prompt.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv-web\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py (Join-Path $root "webapp\run.py") @args
exit $LASTEXITCODE
