param(
    [Parameter(Mandatory = $true)][string]$VersionId,
    [Parameter(Mandatory = $true)][string]$FileId,
    [Parameter(Mandatory = $true)][string]$OutName,
    [string]$Dir = "G:\Comfy-Desktop\ComfyUI-Shared\models\loras"
)

$token = $env:CIVITAI_TOKEN
if (-not $token) {
    $tokenFile = Join-Path $env:USERPROFILE ".civitai\token"
    if (Test-Path $tokenFile) { $token = (Get-Content $tokenFile -Raw).Trim() }
}
if (-not $token) {
    Write-Error "No token: set CIVITAI_TOKEN or put the key into %USERPROFILE%\.civitai\token"
    exit 2
}

if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir -Force | Out-Null }
$out = Join-Path $Dir $OutName

if ((Test-Path $out) -and ((Get-Item $out).Length -gt 1MB)) {
    Write-Output ("skip, already there: " + $OutName)
    exit 0
}

$url = "https://civitai.com/api/download/models/$VersionId" + "?fileId=$FileId" + "&token=$token"
& curl.exe -L --fail -s -o $out $url
if ($LASTEXITCODE -ne 0) {
    if (Test-Path $out) { Remove-Item $out -Force }
    Write-Error ("download failed, curl exit " + $LASTEXITCODE + " (check token and Early Access)")
    exit 1
}

$gb = (Get-Item $out).Length / 1GB
if ($gb -lt 0.001) {
    Remove-Item $out -Force
    Write-Error "file is too small, likely an error page instead of weights"
    exit 1
}
Write-Output ("OK  " + $OutName + "  " + [math]::Round($gb, 2) + " GB")
