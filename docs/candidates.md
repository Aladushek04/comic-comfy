# Кандидаты на добавление (не скачаны)

Подобрано под текущие воркфлоу. Все LoRA — под Illustrious, встают на `WAI-Nsfw-Illustrious-17`
без конфликтов. Скачивание — `tools\civitai-dl.ps1 -VersionId -FileId -OutName`.

## Лечат текущий брак

| Файл | База | Размер | Триггер | Зачем |
|---|---|---|---|---|
| `detailed hand focus style illustriousXL v1.1` | Illustrious | 0.43 | — | кисти; 314k загрузок |
| `detailed skin style illustriousXL v1` | Illustrious | 0.43 | `detailed` | лицо, кожа, тело одним весом |

- Hands XL: `VersionId 2212079`, `FileId 2105147` — https://civitai.com/models/200255?modelVersionId=2212079
- Detailed Perfection: `VersionId 1506333`, `FileId 1406464` — https://civitai.com/models/411088?modelVersionId=1506333

## Чекпойнты-альтернативы

| Файл | Размер | Чем отличается от WAI |
|---|---|---|
| `hassakuV3_4.safetensors` | 6.46 | ярче палитра и теплее свет; 523k загрузок |
| `PornMaster-Pro-noob-V6-VAE.safetensors` | 6.62 | явная анатомия без подбора LoRA |

- Hassaku XL: `VersionId 2615702`, `FileId 2503211` — https://civitai.com/models/140272?modelVersionId=2615702
- PornMaster-Pro: `VersionId 2531839`, `FileId 2419609` — https://civitai.com/models/1045588?modelVersionId=2531839

## Комикс буквально: панели и бабблы

| Файл | Размер | Триггер | Что даёт |
|---|---|---|---|
| `FComic_1to1000_IL_V2.safetensors` | 0.21 | `hcr_generator, comic, Multi view, Frame, Text, speech bubble` | раскладка на панели, кадры, реплики |
| `cartoon Comic book page style illustriousXL v1` | 0.43 | `cartoon` | цельная страница комикса |

- Hentai Comic Generator: `VersionId 2166652`, `FileId 2059833` — https://civitai.com/models/585589?modelVersionId=2166652
- Comic Book Page: `VersionId 1464065`, `FileId 1365306` — https://civitai.com/models/462611?modelVersionId=1464065

Это первый кандидат на отдельный воркфлоу `09` — страница с панелями вместо одиночного кадра.

## Редактор Qwen

| Файл | База | Размер | Что даёт |
|---|---|---|---|
| `Qwen4Play2512_v2.safetensors` | Qwen | 0.55 | all-in-one NSFW-правки, 51k загрузок |

- QWEN 4 PLAY: `VersionId 3061098`, `FileId 2939635` — https://civitai.com/models/2004155?modelVersionId=3061098

Альтернатива нашему `MCNL` в слое 2. Сравнивать на одной и той же правке.

## Запасные стили

| Файл | Размер | Триггер | Манера |
|---|---|---|---|
| `Style_soph-FernandoV2-ILXL.safetensors` | 0.21 | `f3rn4nd0` | западный комикс, толстые линии, ресницы |
| `Cherry-gig Western Style` | 0.43 | `chxrrygxg` | западный стиль, мягче |

- Fernando: `VersionId 2076284`, `FileId 1972396` — https://civitai.com/models/405747?modelVersionId=2076284

Ближайший конкурент `thepit2`: если он даст более «журнальный» вид, можно держать оба и переключать.
