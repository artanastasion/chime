# Chime — музыкальная платформа для авторов видео

Каталог музыки, который авторы видео смогут просматривать и использовать в
своих роликах с учётом условий лицензирования.

Стек: Python 3.12, FastAPI, SQLAlchemy 2 (async, asyncpg), Alembic,
RustFS (S3-совместимое объектное хранилище, доступ через MinIO SDK),
Docker Compose, `uv`.

## 1. Необходимые инструменты

- [Python 3.12](https://www.python.org/) (например, через pyenv)
- [uv](https://docs.astral.sh/uv/) — управление зависимостями и окружением
- Docker и Docker Compose — локальные PostgreSQL и RustFS

## 2. Установка зависимостей

```bash
cp .env.example .env   # при первом клонировании; заполните свои значения
uv sync
```

Все команды ниже выполняются через `uv run ...` из корня проекта.

## 3. Запуск PostgreSQL и RustFS

```bash
docker compose up -d
```

Поднимаются два сервиса:

| Сервис    | Адрес из приложения на хосте | Назначение                  |
|-----------|------------------------------|-----------------------------|
| PostgreSQL| `127.0.0.1:5432`*            | метаданные и связи каталога |
| RustFS    | `127.0.0.1:9000` (S3 API)    | бинарное содержимое медиа   |

\* Если порт 5432 на хосте занят (например, системной PostgreSQL), задайте
`POSTGRES_PORT=5433` в `.env` — Compose опубликует контейнер на нём, а
приложение прочитает настройку из окружения.

Консоль RustFS (веб-интерфейс): **http://127.0.0.1:9001**
(логин/пароль — значения `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY` из `.env`).

Проверка состояния: `docker compose ps`.

## 4. Переменные окружения

Конфигурация приложения — `app/core/config.py` (pydantic-settings), читает
`.env`. Основные переменные (см. `.env.example`):

- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`,
  `POSTGRES_PORT` — подключение к PostgreSQL;
- `RUSTFS_ACCESS_KEY`, `RUSTFS_SECRET_KEY` — учётные данные S3;
- `RUSTFS_ENDPOINT` — адрес S3 API **как его видит приложение**
  (`127.0.0.1:9000` при запуске на хосте, `storage:9000` — из контейнера);
- `RUSTFS_SECURE` — `true` для HTTPS;
- `RUSTFS_BUCKET_MEDIA` — имя основного бакета (`music-media`).

## 5. Миграции (Alembic)

Alembic — единственный штатный способ изменения схемы PostgreSQL.
URL базы строится из настроек приложения в `migrations/env.py`, пароли в
`alembic.ini` не хранятся.

```bash
uv run alembic upgrade head                                  # применить миграции
uv run alembic revision --autogenerate -m "описание изменения" # новая миграция
uv run alembic current                                       # текущая ревизия
uv run alembic history                                       # история ревизий
uv run alembic check                                         # есть ли расхождения схема/модели
```

Приложение никогда не создаёт таблицы само (`Base.metadata.create_all`
не используется) — только Alembic.

## 6. Запуск FastAPI

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Документация API: http://127.0.0.1:8000/docs

При старте приложение проверяет доступность RustFS и идемпотентно создаёт
бакет `music-media`, если его нет. Если RustFS недоступен — запуск
завершается понятной ошибкой, а не «тихим» деградированным состоянием.

## 7. Health check

```bash
curl http://127.0.0.1:8000/health
```

Ответ отдельно отражает состояние API, PostgreSQL и RustFS:

```json
{"status": "ok", "checks": {"api": "ok", "postgres": "ok", "storage": "ok"}}
```

При недоступности любой из зависимостей возвращается `503` с описанием
ошибки по каждой проверке (без секретов).

## 8. Организация объектного хранилища

Используется один **приватный** бакет `music-media`. Типы файлов
разделяются логическими префиксами ключей (это не папки в ФС):

```text
music-media/
└── music/
    ├── audio/
    │   └── {track_id}/
    │       ├── original/
    │       │   └── source_audio.<ext>     # исходная запись
    │       └── renditions/
    │           └── {format}_{bitrate}.<ext>  # версии для воспроизведения
    ├── lyrics/
    │   └── {track_id}/
    │       └── original.txt               # оригинальный текст (UTF-8)
    └── covers/
        ├── albums/{album_id}/cover.<ext>
        └── tracks/{track_id}/cover.<ext>
```

- Расширение файла берётся из имени загружаемого файла (безопасная
  нормализация: строчные буквы/цифры), само имя пользователя в ключ не
  входит.
- Повторная загрузка в занятый «слот» ключа отклоняется сервисом —
  существующий объект не перезаписывается молча.
- Бакет не делается публичным; раздача — по короткоживущим
  presigned-ссылкам (`StorageService.presigned_get_url`, 15 минут),
  ссылки в БД не сохраняются.
- Хранилище — RustFS; доступ выполняется через S3-совместимый MinIO SDK
  (пакет `minio`).

## 9. Что где хранится

| PostgreSQL (`music_platform`)                | RustFS (бакет `music-media`)          |
|----------------------------------------------|----------------------------------------|
| `users` — учётные записи                     | аудиозаписи треков (original + renditions) |
| `artists` — исполнители                      | тексты песен (lyrics)                  |
| `albums` — релизы (тип, статус, дата)        | обложки альбомов и треков              |
| `tracks` — метаданные треков (длительность, номер, explicit, статус) | |
| `track_artists` — связи трек ↔ исполнитель с ролью |                       |
| `storage_objects` — регистрация объектов: бакет, ключ, MIME, размер, sha256, статус | |
| `media_assets` — связь актива с треком/альбомом (тип, язык, формат, битрейт, is_primary) | |

Бинарное содержимое в PostgreSQL не хранится (никаких `BYTEA`).
`object_key` — внутренний ключ объекта, а не публичная ссылка.
Контрольная сумма — SHA-256, вычисляется приложением; ETag S3 как
замена SHA-256 не используется.

Правила целостности `media_assets` (по спецификации):

1. `audio` и `lyrics` привязываются только к треку;
2. `cover` — к треку или к альбому;
3. ровно одна цель у каждого актива (CHECK-ограничения
   `ck_media_assets_single_catalog_target` и
   `ck_media_assets_type_target_compatible`);
4. правило «у опубликованного трека есть хотя бы один доступный
   аудиоактив» — бизнес-логика приложения, не схема.

## 10. Архитектура хранилища в коде

```text
app/
├── core/config.py            # настройки (pydantic-settings)
├── db/base.py                # declarative Base + naming convention
├── db/session.py             # async engine + session factory
├── models/                   # ORM-модели каталога и медиа
│   ├── user.py, artist.py, album.py, track.py
│   ├── track_artist.py       # связи трек ↔ исполнитель (роли)
│   ├── storage_object.py     # регистрация объектов RustFS
│   └── media_asset.py        # связь активов с объектами
├── storage/client.py         # общий экземпляр MinIO-клиента (к RustFS)
├── storage/schemas.py        # UploadResult / ObjectInfo
├── storage/service.py        # StorageService: async-фасад над S3 SDK
├── services/media_assets.py  # MediaService: координация PG <-> RustFS
└── main.py                   # FastAPI, lifespan, /health
```

`StorageService` выполняет все блокирующие вызовы SDK через
`asyncio.to_thread` и ничего не знает о БД. `MediaService` реализует
двухшаговую модель: `register_object()` **сначала резервирует слот**
`(bucket, object_key)` строкой со статусом `pending` (UNIQUE-ограничение
атомарно арбитрирует конкурентные загрузки — проигравший получает ошибку
до какого-либо `put_object`, перезапись исключена), затем загружает файл
и фиксирует статус `available`; `create_asset()` связывает объект с
треком/альбомом. Высокоуровневые методы (`upload_track_audio`,
`upload_lyrics`, `upload_cover`) объединяют оба шага, валидируют вход
**до** загрузки и компенсируют сбои: объект и строка удаляются, а если
компенсация не удалась — строка помечается `failed` (обнаруживаемое
состояние: `status != 'available'`) и бинарник логируется как
`ORPHANED OBJECT`. Действует модель «один объект — один актив»
(UNIQUE `media_assets.object_id`).

Согласованность (общей транзакции у PostgreSQL и S3 нет и не заявляется):

- при загрузке: сначала объект в RustFS, затем строка в БД; сбой записи
  → компенсационное удаление объекта, сбой удаления → пометка `failed`
  + лог `ORPHANED OBJECT`;
- при удалении: сначала объект, затем строки; сбой удаления строк →
  строки остаются для повторяемой сверки (лог-сообщение);
- `media_assets` → `tracks`/`albums` с `ON DELETE RESTRICT`: случайное
  удаление записи каталога с медиа невозможно — сначала явная обработка
  активов через `MediaService.delete_asset`.

## 11. Служебные скрипты

Проверочные скрипты живут в `scripts/` и запускаются из корня проекта:

```bash
# Сквозная проверка каталога + RustFS: создаёт тестовые данные,
# загружает 5 медиаобъектов, проверяет ключи/чексуммы/ограничения,
# компенсацию ошибок и удаляет всё за собой.
uv run python scripts/e2e_check.py

# Полная очистка тестовых данных: удаляет все объекты, зарегистрированные
# в storage_objects, и очищает таблицы каталога.
uv run python scripts/cleanup_test_data.py
```

Скрипты подключают пакет `app` самостоятельно (через `sys.path`),
поэтому их можно запускать из любой директории.

## 13. HTTP API (v1)

Полная интерактивная документация — Swagger UI: http://127.0.0.1:8000/docs
(OpenAPI: `/openapi.json`). Все маршруты — под префиксом `/api/v1`.

### Каталог

| Метод и путь | Назначение |
|---|---|
| `POST /api/v1/artists` · `GET` (список) · `GET/{id}` · `PATCH/{id}` | исполнители |
| `POST /api/v1/albums` · `GET` (список) · `GET/{id}` (с треками и артистами) · `PATCH/{id}` | альбомы |
| `POST /api/v1/tracks` · `GET` (список) · `GET/{id}` (с артистами и альбомом) · `PATCH/{id}` | треки |
| `POST /api/v1/tracks/{id}/artists` | связь трек ↔ исполнитель (роль, порядок) |
| `DELETE /api/v1/tracks/{id}/artists/{track_artist_id}` | удаление конкретной связи |

Фильтры списков: `status`, `name_icontains`/`title_icontains`,
`album_type`, `album_id`, `artist_id` (через треки, без дублей),
`explicit`; пагинация `limit` (1–100, по умолчанию 20) и `offset`;
ответ — конверт `{items, total, limit, offset}`.

### Медиа

| Метод и путь | Назначение |
|---|---|
| `POST /api/v1/tracks/{id}/audio` | аудио: без `audio_bitrate` — слот `original/`, с `audio_format`+`audio_bitrate` — `renditions/{format}_{bitrate}` (multipart + form-поля) |
| `POST /api/v1/tracks/{id}/lyrics` | текст песни (JSON `{content, language?}`, UTF-8) |
| `POST /api/v1/tracks/{id}/cover`, `POST /api/v1/albums/{id}/cover` | обложки (multipart, `image/*`) |
| `GET /api/v1/tracks/{id}/media?asset_type=` | активы трека |
| `GET /api/v1/media/{asset_id}` | информация об активе (+ `download_url`) |
| `GET /api/v1/media/{asset_id}/download` | **307** на presigned URL (15 мин); RustFS сам отдаёт файл и поддерживает HTTP Range |
| `DELETE /api/v1/media/{asset_id}` | удаление бинарника и обеих строк |

Ответы медиа не содержат `bucket`/`object_key` — только метаданные и
одноразовая подписанная ссылка `download_url` (генерируется при каждом
запросе, в БД не хранится; в пути URL бакет и ключ присутствуют по
необходимости протокола S3).

Лимиты: аудио — 100 MiB, обложки — 20 MiB, тексты — 1 MiB. MIME из
multipart — предварительная проверка; авторитетная валидация форматов,
битрейта и содержимого — в `MediaService`.

### Коды ответов

`201` создание · `200` чтение/изменение · `204` удаление · `404` сущность
из URL не найдена · `409` конфликт (занятый слот ключа, дубль связи,
недоступный актив) · `422` невалидные данные, включая ссылки на
несуществующие `album_id`/`artist_id` в теле запроса (единый контракт);
`502/503` — недоступность RustFS; прочие ошибки — `500` без внутренних
деталей (SQL/S3-подробности только в логах).

### Примеры

```bash
# создать исполнителя и трек
curl -X POST http://127.0.0.1:8000/api/v1/artists \
  -H 'Content-Type: application/json' -d '{"name": "New Artist"}'

curl -X POST http://127.0.0.1:8000/api/v1/tracks \
  -H 'Content-Type: application/json' \
  -d '{"title": "Song", "duration_seconds": 180}'

# загрузить оригинальное аудио
curl -X POST http://127.0.0.1:8000/api/v1/tracks/<track_id>/audio \
  -F 'file=@master.flac;type=audio/flac'

# текст песни
curl -X POST http://127.0.0.1:8000/api/v1/tracks/<track_id>/lyrics \
  -H 'Content-Type: application/json' \
  -d '{"content": "ла-ла-ла", "language": "ru"}'
```

Известные ограничения: повторная загрузка в занятый слот (тот же тип
файла и расширение) возвращает 409 — сначала удалите актив; смена
расширения файла означает другой слот; `is_primary` не проверяется на
уникальность (бизнес-правило публикации — вне текущего объёма);
клиент должен иметь сетевой доступ к `RUSTFS_ENDPOINT` для перехода по
307-ссылкам.

## 14. Тесты

Интеграционные тесты (`tests/`) покрывают и надёжность `MediaService`
(компенсации сбоев, конкурентные загрузки, ограничения БД), и HTTP API
каталога и медиа. Требуют запущенных PostgreSQL и RustFS:

```bash
uv run pytest tests/ -v      # 35 тестов: 15 сервис + 20 API
```



