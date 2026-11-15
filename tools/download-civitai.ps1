# Download the Civitai files every workflow needs.
#
#   powershell -ExecutionPolicy Bypass -File tools\download-civitai.ps1
#   powershell -ExecutionPolicy Bypass -File tools\download-civitai.ps1 -Verify
#
# Needs a token: CIVITAI_TOKEN or %USERPROFILE%\.civitai\token.
# Target dir: -Root, else COMFY_MODELS, else base_path from extra_model_paths.yaml.
# Each file is fetched by civitai-dl.ps1: .part, resume, exact size, rename.
# Optional extras that no workflow uses (NovaComic, digitalInkedComic,
# Clusterfuck) are NOT here - see DOWNLOADS.md, part 2.
# ASCII only: Windows PowerShell 5.1 parses .ps1 as ANSI.
param(
    [string]$Root = $env:COMFY_MODELS,
    [switch]$Verify,
    [switch]$Force
)

$ErrorActionPreference = "Continue"
$repo = Split-Path $PSScriptRoot -Parent

if (-not $Root) {
    $yaml = Join-Path $repo "extra_model_paths.yaml"
    if (Test-Path $yaml) {
        $line = Select-String -Path $yaml -Pattern "^\s*base_path\s*:\s*(.+?)\s*$" | Select-Object -First 1
        if ($line) { $Root = $line.Matches[0].Groups[1].Value.Trim() }
    }
}
if (-not $Root) {
    Write-Host "No target dir. Use -Root, set COMFY_MODELS, or set base_path in extra_model_paths.yaml."
    exit 2
}
Write-Host ("Models: " + $Root)

$items = @(
    @{ id = 2883731; file = 2763986; dir = "checkpoints"; name = "WAI-Nsfw-Illustrious-17.safetensors" },
    @{ id = 1868373; file = 1768210; dir = "loras"; name = "thepit2_1768210.safetensors" },
    @{ id = 2261840; file = 2154148; dir = "loras"; name = "Sexy_Detailer.safetensors" },
    @{ id = 2305750; file = 2197721; dir = "loras"; name = "P477Y.safetensors" },
    @{ id = 2212079; file = 2105147; dir = "loras\detail"; name = "hands_xl.safetensors" },
    @{ id = 1776335; file = 1677196; dir = "loras\style"; name = "thepit_comic.safetensors" },
    @{ id = 1829222; file = 1730159; dir = "loras\style"; name = "johnpersons_original.safetensors" },
    @{ id = 1442607; file = 1345170; dir = "loras\style"; name = "bimbofusion.safetensors" },
    @{ id = 2140775; file = 2034567; dir = "loras"; name = "GTAV_IL_MIX.safetensors" }
)

if ($Verify) {
    $token = $env:CIVITAI_TOKEN
    if (-not $token) {
        $tf = Join-Path $env:USERPROFILE ".civitai\token"
        if (Test-Path $tf) { $token = (Get-Content $tf -Raw).Trim() }
    }
    $headers = @{ "User-Agent" = "Mozilla/5.0" }
    if ($token) { $headers["Authorization"] = "Bearer $token" }
    $bad = 0
    foreach ($i in $items) {
        try {
            $v = Invoke-RestMethod -Uri ("https://civitai.com/api/v1/model-versions/" + $i.id) -Headers $headers -Method Get
            $f = $v.files | Where-Object { [string]$_.id -eq [string]$i.file } | Select-Object -First 1
            if ($f) {
                Write-Host ("SIZE  " + [math]::Round($f.sizeKB / 1024 / 1024, 2) + " GB  " + $i.name)
            } else {
                Write-Host ("FAIL  " + $i.name + ": fileId " + $i.file + " is not in version " + $i.id)
                $bad++
            }
        } catch {
            Write-Host ("FAIL  " + $i.name + ": " + $_.Exception.Message)
            $bad++
        }
        Start-Sleep -Milliseconds 300
    }
    Write-Host ("Checked: ok " + ($items.Count - $bad) + ", failed " + $bad)
    exit $(if ($bad -gt 0) { 1 } else { 0 })
}

$bad = 0
foreach ($i in $items) {
    $dir = Join-Path $Root $i.dir
    $cli = @("-VersionId", $i.id, "-FileId", $i.file, "-OutName", $i.name, "-Dir", $dir)
    if ($Force) { $cli += "-Force" }
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "civitai-dl.ps1") @cli
    if ($LASTEXITCODE -ne 0) { $bad++ }
}

Write-Host ""
if ($bad -gt 0) {
    Write-Host ("Finished with errors: " + $bad + ". Rerun to resume.")
    exit 1
}
Write-Host "Done. Check layout: python tools\check_install.py"
exit 0
