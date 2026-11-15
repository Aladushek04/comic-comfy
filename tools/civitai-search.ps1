param(
    [Parameter(Mandatory = $true)][string]$Query,
    [string]$Types = "",
    [string]$Base = "",
    [switch]$Nsfw,
    [int]$Limit = 10
)

# Token is read here and never printed.
$token = $env:CIVITAI_TOKEN
if (-not $token) {
    $f = Join-Path $env:USERPROFILE ".civitai\token"
    if (Test-Path $f) { $token = (Get-Content $f -Raw).Trim() }
}

$headers = @{ "User-Agent" = "Mozilla/5.0" }
if ($token) { $headers["Authorization"] = "Bearer $token" }

$url = "https://civitai.com/api/v1/models?limit=$Limit&query=" + [uri]::EscapeDataString($Query)
if ($Types) { $url += "&types=$Types" }
if ($Base) { $url += "&baseModels=" + [uri]::EscapeDataString($Base) }
if ($Nsfw) { $url += "&nsfw=true" }
$url += "&sort=Most%20Downloaded"

try {
    $r = Invoke-RestMethod -Uri $url -Headers $headers -Method Get
} catch {
    Write-Error ("request failed: " + $_.Exception.Message)
    exit 1
}

Write-Output ("query: " + $Query + "  found: " + $r.items.Count)
foreach ($m in $r.items) {
    $v = $m.modelVersions[0]
    $file = $v.files[0]
    $gb = 0
    if ($file -and $file.sizeKB) { $gb = [math]::Round($file.sizeKB / 1024 / 1024, 2) }
    $trig = ""
    if ($v.trainedWords) { $trig = ($v.trainedWords -join ", ") }
    if ($trig.Length -gt 70) { $trig = $trig.Substring(0, 70) + "..." }
    Write-Output ("--- " + $m.name)
    Write-Output ("    type=" + $m.type + "  nsfw=" + $m.nsfw + "  base=" + $v.baseModel + "  size=" + $gb + " GB")
    Write-Output ("    versionId=" + $v.id + "  fileId=" + $file.id + "  file=" + $file.name)
    if ($trig) { Write-Output ("    trigger=" + $trig) }
    Write-Output ("    page=https://civitai.com/models/" + $m.id + "?modelVersionId=" + $v.id)
}
