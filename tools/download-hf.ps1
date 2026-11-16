# Download models from Hugging Face.
#
#   powershell -ExecutionPolicy Bypass -File tools\download-hf.ps1
#   powershell -ExecutionPolicy Bypass -File tools\download-hf.ps1 -Verify
#   powershell -ExecutionPolicy Bypass -File tools\download-hf.ps1 -Root <dir>
#
# Target dir: -Root, else COMFY_MODELS, else base_path from extra_model_paths.yaml.
# Every file goes to <name>.part and is renamed only after its size matches the
# server, so an interrupted download never looks complete. A rerun resumes from
# where it stopped (curl -C -) instead of starting over.
# ASCII only: Windows PowerShell 5.1 parses .ps1 as ANSI, Cyrillic breaks it.
param(
    [string]$Root = $env:COMFY_MODELS,
    [switch]$Verify,
    [switch]$Force,
    [string]$Only = ""
)

$ErrorActionPreference = "Continue"
$repo = Split-Path $PSScriptRoot -Parent

function Get-RemoteSize([string]$url) {
    $head = & curl.exe -sIL --fail $url 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    $m = $head | Select-String -Pattern "^content-length:\s*(\d+)" | Select-Object -Last 1
    if ($m) { return [int64]$m.Matches[0].Groups[1].Value }
    return $null
}

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

$pairs = @(
    # ---- Layer 1: SDXL / Illustrious ----
    @{ url = "https://huggingface.co/Laxhar/noobai-XL-1.1/resolve/main/NoobAI-XL-v1.1.safetensors"; dir = "checkpoints"; name = "NoobAI-XL-v1.1.safetensors" },
    @{ url = "https://huggingface.co/xinsir/controlnet-union-sdxl-1.0/resolve/main/diffusion_pytorch_model_promax.safetensors"; dir = "controlnet"; name = "controlnet-union-sdxl-promax.safetensors" },
    @{ url = "https://huggingface.co/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors"; dir = "ipadapter"; name = "ip-adapter-plus_sdxl_vit-h.safetensors" },
    @{ url = "https://huggingface.co/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors"; dir = "clip_vision"; name = "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors" },
    @{ url = "https://huggingface.co/lokCX/4x-Ultrasharp/resolve/main/4x-UltraSharp.pth"; dir = "upscale_models"; name = "4x-UltraSharp.pth" },
    # Depth Anything 3: native ComfyUI nodes (comfy_extras/nodes_depth_anything_3.py).
    # Needs geometry_estimation in extra_model_paths.yaml, otherwise the loader
    # does not see the file and the graph fails on "value not in list".
    @{ url = "https://huggingface.co/Comfy-Org/Depth-Anything-3/resolve/main/geometry_estimation/depth_anything_3_mono_large.safetensors"; dir = "geometry_estimation"; name = "depth_anything_3_mono_large.safetensors" },

    # ---- Layer 2: Qwen-Image-Edit 2511 uncensored ----
    @{ url = "https://huggingface.co/ChrisColeTech/qwen-image-edit-uncensored-GGUF/resolve/main/split/diffusion_models/qwen-image-edit-2511-uncensored-Q4_K_M.gguf"; dir = "diffusion_models"; name = "qwen-image-edit-2511-uncensored-Q4_K_M.gguf" },
    @{ url = "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"; dir = "text_encoders"; name = "qwen_2.5_vl_7b_fp8_scaled.safetensors" },
    @{ url = "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors"; dir = "vae"; name = "qwen_image_vae.safetensors" },
    @{ url = "https://huggingface.co/ScottzillaSystems/qwen-image-edit-plus-nsfw-lora/resolve/main/qwen-image-edit-plus-nsfw-lora.safetensors"; dir = "loras"; name = "qwen-image-edit-plus-nsfw-lora.safetensors" },
    @{ url = "https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"; dir = "loras"; name = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors" },

    # ---- Layer 3: Noct Q Anime (Qwen-Image 2.1) ----
    @{ url = "https://huggingface.co/Noctaluna/Noct-Q-Anime-Uncensored-Qwen-Image-2.1/resolve/main/NoctQA_V1_int8_convrot.safetensors"; dir = "diffusion_models"; name = "NoctQA_V1_int8_convrot.safetensors" },
    @{ url = "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/text_encoders/qwen3vl_8b_int8_convrot.safetensors"; dir = "text_encoders"; name = "qwen3vl_8b_int8_convrot.safetensors" },
    @{ url = "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors"; dir = "vae"; name = "qwen_image_2.1_vae_bf16.safetensors" }
)

# Authors' example workflows go to their own folder, not next to our builds.
$examples = @(
    @{ url = "https://huggingface.co/ChrisColeTech/qwen-image-edit-uncensored-GGUF/resolve/main/workflow_example/qwen_image_edit_nsfw.json"; dir = "workflows\reference"; name = "qwen_image_edit_nsfw.json"; repo = $true },
    @{ url = "https://huggingface.co/Noctaluna/Noct-Q-Anime-Uncensored-Qwen-Image-2.1/resolve/main/NoctQ_Anime_V1_workflow.json"; dir = "workflows\reference"; name = "NoctQ_Anime_V1_workflow.json"; repo = $true }
)

function Get-Gb([int64]$bytes) { if ($null -eq $bytes) { return "?" } return [math]::Round($bytes / 1GB, 2) }

function Save-Item($item, [string]$base) {
    $dest = if ($item.repo) {
        Join-Path $repo (Join-Path $item.dir $item.name)
    } else {
        Join-Path (Join-Path $base $item.dir) $item.name
    }
    $part = $dest + ".part"
    $want = Get-RemoteSize $item.url

    if ($Verify) {
        if ($want) {
            Write-Host ("SIZE  " + (Get-Gb $want) + " GB  " + $item.name)
            return $true
        }
        Write-Host ("FAIL  " + $item.url)
        return $false
    }

    New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force | Out-Null
    if ($Force) {
        if (Test-Path $dest) { Remove-Item $dest -Force }
        if (Test-Path $part) { Remove-Item $part -Force }
    } elseif ((Test-Path $dest) -and $want -and (Get-Item $dest).Length -eq $want) {
        Write-Host ("SKIP  " + $dest + "  (" + (Get-Gb $want) + " GB, size matches)")
        return $true
    } elseif ((Test-Path $dest) -and -not $want) {
        Write-Host ("KEEP  " + $dest + "  (server size unknown, cannot verify)")
        return $true
    } elseif ((Test-Path $dest) -and $want -and (Get-Item $dest).Length -ne $want) {
        # Partial file: keep the bytes and resume from them.
        if (Test-Path $part) { Remove-Item $part -Force }
        Move-Item $dest $part -Force
        Write-Host ("RESUME " + $dest + "  (" + (Get-Gb (Get-Item $part).Length) + " of " + (Get-Gb $want) + " GB)")
    }

    Write-Host ("GET   " + $dest)
    & curl.exe -L --fail --retry 5 --retry-delay 5 --retry-all-errors -C - --progress-bar -o $part $item.url
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("FAIL  " + $item.url + "  (kept " + $part + ", rerun continues)")
        return $false
    }
    $got = (Get-Item $part).Length
    if ($want -and $got -ne $want) {
        Write-Host ("FAIL  " + $item.name + ": got " + $got + " bytes, expected " + $want + " - kept .part")
        return $false
    }
    Move-Item $part $dest -Force
    Write-Host ("OK    " + $dest + "  " + (Get-Gb $got) + " GB")
    return $true
}

$ok, $bad = 0, 0
$list = $pairs + $examples
if ($Only) {
    $list = $list | Where-Object { $_.name -like ("*" + $Only + "*") }
    if (-not $list) { Write-Host ("Nothing matches -Only " + $Only); exit 2 }
}
foreach ($i in $list) {
    if (Save-Item $i $Root) { $ok++ } else { $bad++ }
}

Write-Host ""
if ($Verify) {
    Write-Host ("Checked links: ok " + $ok + ", failed " + $bad)
    exit $(if ($bad -gt 0) { 1 } else { 0 })
}

if ($bad -gt 0) {
    Write-Host ("Finished with errors: ok " + $ok + ", failed " + $bad + ". Rerun to resume.")
    exit 1
}
Write-Host ("Done: " + $ok + " files. Check layout: python tools\check_install.py")
exit 0
