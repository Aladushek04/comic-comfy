# Download one Civitai file by versionId/fileId.
#
#   tools\civitai-dl.ps1 -VersionId 2140775 -FileId 2034567 -OutName GTAV_IL_MIX.safetensors
#
# Target dir: -Dir, else $env:COMFY_MODELS\loras, else base_path from extra_model_paths.yaml.
# Downloads to <name>.part and renames only after the size matches the API, so a
# broken download never looks finished; a rerun resumes with curl -C -.
# ASCII only: Windows PowerShell 5.1 parses .ps1 as ANSI.
param(
    [Parameter(Mandatory = $true)][string]$VersionId,
    [Parameter(Mandatory = $true)][string]$FileId,
    [Parameter(Mandatory = $true)][string]$OutName,
    [string]$Dir = "",
    [switch]$Force
)

$ErrorActionPreference = "Continue"

$token = $env:CIVITAI_TOKEN
if (-not $token) {
    $tokenFile = Join-Path $env:USERPROFILE ".civitai\token"
    if (Test-Path $tokenFile) { $token = (Get-Content $tokenFile -Raw).Trim() }
}
if (-not $token) {
    Write-Error "No token: set CIVITAI_TOKEN or put the key into %USERPROFILE%\.civitai\token"
    exit 2
}

if (-not $Dir) {
    if ($env:COMFY_MODELS) {
        $Dir = Join-Path $env:COMFY_MODELS "loras"
    } else {
        $yaml = Join-Path (Split-Path $PSScriptRoot -Parent) "extra_model_paths.yaml"
        if (Test-Path $yaml) {
            $line = Select-String -Path $yaml -Pattern "^\s*base_path\s*:\s*(.+?)\s*$" | Select-Object -First 1
            if ($line) { $Dir = Join-Path $line.Matches[0].Groups[1].Value.Trim() "loras" }
        }
    }
}
if (-not $Dir) {
    Write-Error "No target dir. Use -Dir, set COMFY_MODELS, or set base_path in extra_model_paths.yaml."
    exit 2
}
if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir -Force | Out-Null }
$out = Join-Path $Dir $OutName
$part = $out + ".part"

# Exact size from the API: a size check beats "bigger than 1 MB".
$headers = @{ "User-Agent" = "Mozilla/5.0"; "Authorization" = "Bearer $token" }
$want = $null
try {
    $v = Invoke-RestMethod -Uri ("https://civitai.com/api/v1/model-versions/" + $VersionId) -Headers $headers -Method Get
    $file = $v.files | Where-Object { [string]$_.id -eq [string]$FileId } | Select-Object -First 1
    if ($file -and $file.sizeKB) { $want = [int64]($file.sizeKB * 1024) }
} catch {
    Write-Output "size lookup failed, will verify by minimum only: $($_.Exception.Message)"
}

if ($Force) {
    if (Test-Path $out) { Remove-Item $out -Force }
    if (Test-Path $part) { Remove-Item $part -Force }
} elseif ((Test-Path $out) -and $want -and (Get-Item $out).Length -eq $want) {
    Write-Output ("skip, size matches: " + $OutName)
    exit 0
} elseif ((Test-Path $out) -and -not $want -and (Get-Item $out).Length -gt 1MB) {
    Write-Output ("skip, already there (size unknown): " + $OutName)
    exit 0
} elseif ((Test-Path $out) -and $want -and (Get-Item $out).Length -ne $want) {
    if (Test-Path $part) { Remove-Item $part -Force }
    Move-Item $out $part -Force
    Write-Output ("resume: " + $OutName)
}

$url = "https://civitai.com/api/download/models/$VersionId" + "?fileId=$FileId" + "&token=$token"
& curl.exe -L --fail --retry 5 --retry-delay 5 --retry-all-errors -C - -s -o $part $url
if ($LASTEXITCODE -ne 0) {
    Write-Error ("download failed, curl exit " + $LASTEXITCODE + " (check token and Early Access; kept .part)")
    exit 1
}

$got = (Get-Item $part).Length
if ($want -and $got -ne $want) {
    Write-Error ("size " + $got + " != " + $want + ", kept .part - rerun to resume")
    exit 1
}
if ($got -lt 1MB) {
    Remove-Item $part -Force
    Write-Error "file is too small, likely an error page instead of weights"
    exit 1
}
Move-Item $part $out -Force
Write-Output ("OK  " + $OutName + "  " + [math]::Round($got / 1GB, 2) + " GB")
