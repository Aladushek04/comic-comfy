# Что скачать

Две части: с Hugging Face (~52 ГБ) и с Civitai (~13.7 ГБ). Всего ~66 ГБ из 265 свободных на G:.

Класть сразу в общие папки ComfyUI — тогда ничего переносить не придётся:

```
G:\Comfy-Desktop\ComfyUI-Shared\models\
├── checkpoints       WAI, NoobAI, Nova Comic
├── loras             Sexy Detailer, 2 стилевые LoRA, MCNL
├── controlnet        Union SDXL promax
├── ipadapter         ip-adapter-plus
├── clip_vision       CLIP-ViT-H
├── upscale_models    4x-UltraSharp
├── diffusion_models  Qwen-Edit Q4, NoctQA
├── text_encoders     qwen_2.5_vl fp8, qwen3vl_8b int8
└── vae               qwen_image_vae, qwen_image_2.1_vae
```

---

## Часть 1. Hugging Face — автоматически

Логин не нужен. Запусти в PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File "G:\AI\comic-comfy\download-hf.ps1"
```

Скрипт сам создаёт папки, пропускает уже скачанное, качает с докачкой при обрыве и в конце печатает список файлов с размерами. Если что-то упало — перезапусти, продолжит с места.

Что он тянет:

| Файл | Папка | Размер |
|---|---|---|
| `NoobAI-XL-v1.1.safetensors` | checkpoints | 7.11 ГБ |
| `controlnet-union-sdxl-promax.safetensors` | controlnet | 2.51 ГБ |
| `ip-adapter-plus_sdxl_vit-h.safetensors` | ipadapter | 0.85 ГБ |
| `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` | clip_vision | 2.53 ГБ |
| `4x-UltraSharp.pth` | upscale_models | 0.07 ГБ |
| `qwen-image-edit-2511-uncensored-Q4_K_M.gguf` | diffusion_models | 11.66 ГБ |
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | text_encoders | 9.38 ГБ |
| `qwen_image_vae.safetensors` | vae | 0.25 ГБ |
| `qwen-image-edit-plus-nsfw-lora.safetensors` | loras | 0.59 ГБ |
| `NoctQA_V1_int8_convrot.safetensors` | diffusion_models | 7.26 ГБ |
| `qwen3vl_8b_int8_convrot.safetensors` | text_encoders | 9.35 ГБ |
| `qwen_image_2.1_vae_bf16.safetensors` | vae | 0.68 ГБ |

Плюс два готовых воркфлоу в `G:\AI\comic-comfy\workflows\`: `04_qwen_instruction_edit.json` и `05_noct_anime_scene.json`.

---

## Часть 2. Civitai — вручную

Нужен аккаунт и вход в браузере. Скачивание идёт напрямую из браузера, без токенов.

| Файл | Куда | Размер | Ссылка |
|---|---|---|---|
| `WAI-Nsfw-Illustrious-17.safetensors` | checkpoints | 6.46 ГБ | https://civitai.com/models/827184?modelVersionId=2883731 |
| `NovaComicILV2.safetensors` | checkpoints | 6.65 ГБ | https://civitai.com/models/1969383?modelVersionId=2431039 |
| `Sexy_Detailer.safetensors` | loras | 0.21 ГБ | https://civitai.com/models/1998306?modelVersionId=2261840 |
| `digitalInkedComic_v1.safetensors` | loras | 0.21 ГБ | https://civitai.com/models/3040352?modelVersionId=3455706 |
| `Clusterfuck_Illustrious-000035.safetensors` | loras | 0.21 ГБ | https://civitai.com/models/154425?modelVersionId=1309345 |

Если Civitai спросит «Early Access» или попросит токен — бери обычную кнопку Download, эти версии бесплатные.

`Nova Comic XL` помечен как SFW-ориентированный: он нужен только для «приличных» кадров и комиксового вида. Если не хочешь тратить 6.65 ГБ — пропусти, WAI справится со всем.

---

## Когда готово

Скажи «готово» — я проверю размеры и целостность, поставлю ноды и соберу оставшиеся воркфлоу (`01`–`03`), затем прогоню тесты и запишу тайминги.

Если какой-то файл не встал — просто напиши, какой, дам зеркало.
