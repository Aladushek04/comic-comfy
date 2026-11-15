# Памятка: доступ к Civitai в этом проекте

Токен не спрашивается заново и не вводится в чат.

- **Где лежит:** `%USERPROFILE%\.civitai\token` (`C:\Users\isoko\.civitai\token`).
  Права: наследование снято, доступ на чтение только у учётной записи `isoko`.
- **Кто читает:** `G:\AI\comic-comfy\tools\civitai-dl.ps1`. Порядок поиска: переменная
  окружения `CIVITAI_TOKEN`, затем файл выше.
- **Как качать:**

```powershell
powershell -ExecutionPolicy Bypass -File G:\AI\comic-comfy\tools\civitai-dl.ps1 `
  -VersionId 2140775 -FileId 2034567 -OutName GTAV_IL_MIX.safetensors
```

`VersionId` и `FileId` берутся из `https://civitai.com/api/v1/model-versions/<VersionId>`
(поле `files[].id`). Если файл уже лежит и он больше 1 МБ — скрипт пропускает загрузку.

- **Ограничения:** часть моделей закрыта Early Access — им не поможет и токен, качать из браузера.
  Анонимный `HEAD` часто отдаёт `200`, а реальный GET — `401`, поэтому проверять только загрузкой.
  Скрипт после скачивания отвергает файлы меньше 1 МБ (вместо весов приходит страница ошибки).

- **Никогда:** не печатать содержимое токена, не копировать его в `G:\AI\comic-comfy`,
  не отправлять в чат и не коммитить.
