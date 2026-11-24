# Где дешевле? — Telegram Mini App

Сравнение цен в Сільпо, АТБ, Варус, Новус, Траш и Велмарт.

## Структура

- `app.py` — веб-сервер Mini App и HTML-интерфейс (вкладки `Поиск` / `Акции` снизу).
- `price_finder.py` — парсеры магазинов, кэш, сортировки, категории акций.
- `bot.py` — запуск бота, чтение переменных окружения и **все обработчики**. Добавляйте новые команды сюда.
- `assets/start.png` — изображение для `/start`.
- `render.yaml` — готовый Blueprint для Render (web + worker).

## Локальный запуск

```bat
pip install -r requirements.txt
python tunnel.py
python app.py
python bot.py
```

Локально бот возьмёт HTTPS-ссылку из `tunnel_url.txt` (если нет `WEBAPP_URL`).
Токен можно задать двумя способами:

1. переменная окружения `BOT_TOKEN`;
2. файл `bot_token.txt` в папке проекта (уже в `.gitignore`).

Файлы `tunnel_url.txt` и `bot_token.txt` в репозиторий не попадут.

Переменные окружения:

| Переменная | Где нужна | Что это |
|---|---|---|
| `BOT_TOKEN` | бот | токен от @BotFather (обязательно) |
| `WEBAPP_URL` | бот | HTTPS-ссылка на web-сервис (на Render подставляется сама) |
| `PORT` | web | Render задаёт автоматически, `app.py` читает его |

Пример локально (PowerShell):

```powershell
$env:WEBAPP_URL="https://ваш-домен.lhr.life"
python bot.py
```

…или просто создайте `bot_token.txt` с токеном и запускайте `python bot.py`.

## Деплой на Render

1. Залейте репозиторий на GitHub.
2. Render → **New → Blueprint** → выберите репозиторий.
3. При создании Blueprint Render запросит `BOT_TOKEN` — вставьте его вручную.
4. `WEBAPP_URL` подставится автоматически из web-сервиса через `fromService`.

Создадутся два сервиса:

- `gde-deeshevle-web` — Mini App и API;
- `gde-deeshevle-bot` — бот с `/start`, кнопкой `Go` и `Інформація`.

### Важно

- Токен бота **больше не зашит в коде**. Если он уже попал в GitHub, отзовите его у @BotFather и создайте новый.
- На бесплатном плане Render web-сервис засыпает — первый запрос после простоя может быть медленным.
- Кэш цен живёт в памяти процесса 5 минут; после рестарта сервиса он очищается.
- Ключи `ATB_MS_*` / `VARUS_MS_*` в `price_finder.py` — публичные ключи frontend API магазинов, не токены бота.

## Кастомная картинка `/start`

Замените `assets/start.png` или задайте в `bot.py`:

```python
START_IMAGE_URL = "https://example.com/start.jpg"
```
