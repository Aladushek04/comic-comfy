$ErrorActionPreference = "Continue"
$root = "G:\Comfy-Desktop\ComfyUI-Shared\models"

$items = @(
  # ---- Layer 1: SDXL / Illustrious ----
  @{ url = "https://huggingface.co/Laxhar/noobai-XL-1.1/resolve/main/NoobAI-XL-v1.1.safetensors"; dest = "$root\checkpoints\NoobAI-XL-v1.1.safetensors"; min = 7000000000 },
  @{ url = "https://huggingface.co/xinsir/controlnet-union-sdxl-1.0/resolve/main/diffusion_pytorch_model_promax.safetensors"; dest = "$root\controlnet\controlnet-union-sdxl-promax.safetensors"; min = 2400000000 },
  @{ url = "https://huggingface.co/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors"; dest = "$root\ipadapter\ip-adapter-plus_sdxl_vit-h.safetensors"; min = 800000000 },
  @{ url = "https://huggingface.co/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors"; dest = "$root\clip_vision\CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"; min = 2400000000 },
  @{ url = "https://huggingface.co/lokCX/4x-Ultrasharp/resolve/main/4x-UltraSharp.pth"; dest = "$root\upscale_models\4x-UltraSharp.pth"; min = 60000000 },

  # ---- Layer 2: Qwen-Image-Edit 2511 uncensored ----
  @{ url = "https://huggingface.co/ChrisColeTech/qwen-image-edit-uncensored-GGUF/resolve/main/split/diffusion_models/qwen-image-edit-2511-uncensored-Q4_K_M.gguf"; dest = "$root\diffusion_models\qwen-image-edit-2511-uncensored-Q4_K_M.gguf"; min = 11000000000 },
  @{ url = "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"; dest = "$root\text_encoders\qwen_2.5_vl_7b_fp8_scaled.safetensors"; min = 9000000000 },
  @{ url = "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors"; dest = "$root\vae\qwen_image_vae.safetensors"; min = 200000000 },
  @{ url = "https://huggingface.co/ScottzillaSystems/qwen-image-edit-plus-nsfw-lora/resolve/main/qwen-image-edit-plus-nsfw-lora.safetensors"; dest = "$root\loras\qwen-image-edit-plus-nsfw-lora.safetensors"; min = 500000000 },
  @{ url = "https://huggingface.co/ChrisColeTech/qwen-image-edit-uncensored-GGUF/resolve/main/workflow_example/qwen_image_edit_nsfw.json"; dest = "G:\AI\comic-comfy\workflows\04_qwen_instruction_edit.json"; min = 1000 },

  # ---- Layer 3: Noct Q Anime (Qwen-Image 2.1) ----
  @{ url = "https://huggingface.co/Noctaluna/Noct-Q-Anime-Uncensored-Qwen-Image-2.1/resolve/main/NoctQA_V1_int8_convrot.safetensors"; dest = "$root\diffusion_models\NoctQA_V1_int8_convrot.safetensors"; min = 7000000000 },
  @{ url = "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/text_encoders/qwen3vl_8b_int8_convrot.safetensors"; dest = "$root\text_encoders\qwen3vl_8b_int8_convrot.safetensors"; min = 9000000000 },
  @{ url = "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors"; dest = "$root\vae\qwen_image_2.1_vae_bf16.safetensors"; min = 600000000 },
  @{ url = "https://huggingface.co/Noctaluna/Noct-Q-Anime-Uncensored-Qwen-Image-2.1/resolve/main/NoctQ_Anime_V1_workflow.json"; dest = "G:\AI\comic-comfy\workflows\05_noct_anime_scene.json"; min = 1000 }
)

foreach ($i in $items) {
  $dir = Split-Path $i.dest -Parent
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  if ((Test-Path $i.dest) -and ((Get-Item $i.dest).Length -ge $i.min)) {
    Write-Host ("SKIP  " + $i.dest)
    continue
  }
  Write-Host ("GET   " + $i.dest)
  & curl.exe -L --fail --retry 3 --retry-delay 5 --progress-bar -o $i.dest $i.url
  if ($LASTEXITCODE -ne 0) { Write-Host ("FAIL  " + $i.url) }
}

Write-Host ""
Write-Host "=== Result (files over 20 MB) ==="
Get-ChildItem $root -Recurse -File -Include *.safetensors,*.gguf,*.pth |
  Where-Object { $_.Length -gt 20MB } |
  Sort-Object FullName |
  ForEach-Object { "{0,8:N2} GB  {1}" -f ($_.Length / 1GB), $_.FullName }
