# DWH, генератор данных и аналитика

Дата: 2026-10-31

## Цель этапа

Показать, что хранилища работают и данные в них живут: добавляются,
изменяются, перемещаются между сущностями и удаляются, а каждое изменение
доходит до аналитического хранилища. Полную бизнес-логику эндпоинтов на этом
этапе не реализуем.

## Что сделано

### Инфраструктура (`docker-compose.yml`, `infra/`)

- PostgreSQL переведён на `wal_level=logical` (нужно для CDC).
- Добавлены сервисы: Redpanda (Kafka API), Redpanda Console, Debezium Connect,
  ClickHouse, Grafana.
- Одноразовые идемпотентные init-контейнеры:
  - `redpanda-topics` — заранее создаёт топики. Без этого консьюмер
    ClickHouse подхватывает новый топик только через ~5 минут (интервал
    обновления метаданных librdkafka);
  - `clickhouse-migrate` — применяет `infra/clickhouse/sql/*.sql` по порядку;
  - `connect-init` — регистрирует коннектор `chime-catalog` через
    `PUT /connectors/{name}/config` (повторный запуск безопасен).
- Секреты коннектора не лежат в конфиге: используется `EnvVarConfigProvider`
  (`${env:POSTGRES_PASSWORD}`).
- Порт Postgres для chime — 5433: порт 5432 занят контейнером другого проекта,
  его не трогаем. Grafana на 3001: порт 3000 занят.

### DWH в ClickHouse (`infra/clickhouse/sql`)

| Файл | Содержимое |
|---|---|
| `001_cdc_changelog.sql` | `cdc_changelog` (журнал всех изменений каталога), `ingest_errors` (dead-letter) |
| `002_catalog_dimensions.sql` | `dim_*` на `ReplacingMergeTree(_version = LSN, _is_deleted)` + MV из журнала |
| `003_catalog_views.sql` | `v_*` (актуальное состояние), `v_track_primary_artist`, `v_track_catalog` |
| `004_events.sql` | `fact_api_requests`, `fact_app_events`, агрегаты `agg_api_minute`, `agg_track_daily` |
| `900_kafka_consumers.sql` | Kafka engine таблицы и MV. Создаются последними, чтобы снапшот не прошёл мимо измерений |

### Приложение (`app/`)

- `app/analytics/publisher.py` — асинхронный Kafka-продюсер (aiokafka) с
  фоновым переподключением. Отказ шины не ломает API.
- `app/analytics/access_log.py` — чистый ASGI middleware: access-лог каждого
  запроса (шаблон маршрута, статус, латентность, байты, контекст устройства)
  и заголовок `X-Request-ID`. Шаблон маршрута восстанавливается из
  `path` + `path_params`, без опоры на внутренние структуры FastAPI.
- `POST /api/v1/events` — приём телеметрии клиента (`app/schemas/events.py`,
  `app/services/events.py`, `app/api/v1/events.py`). Ответ `202` отдаётся
  после подтверждения записи брокером, `503` — если шина недоступна.
  Валидируются окно времени события, лимиты свойств, enum'ы.
- `users` API: `POST/GET /api/v1/users`, `GET/PATCH /api/v1/users/{id}`
  (`app/schemas/user.py`, `app/services/users.py`, `app/api/v1/users.py`).
  Раньше у таблицы `users` не было API, и генератор писал в неё напрямую.
  Теперь все записи о пользователях идут через приложение.
- `app/core/config.py` — настройки Kafka и топиков. Без
  `KAFKA_BOOTSTRAP_SERVERS` аналитика выключена.
- Новые зависимости: `aiokafka` (runtime), `faker` (dev).

### Генератор и симулятор (`simulator/`)

- `seed` — наполняет каталог **только через API**: пользователи, исполнители,
  релизы, треки, связи с ролями, аудио (оригинал + rendition), тексты, обложки.
- `run` — симуляция прод-нагрузки: слушатели (поиск, релизы, просмотр,
  Range-стриминг из RustFS, телеметрия пачками) и студия (новые синглы,
  публикация, переименование, перенос треков между релизами, фиты, замена
  аудио, снятие с публикации, регистрации и деактивации пользователей).
- `demo` — пошаговый сценарий INSERT → UPDATE → MOVE → UPLOAD → CONFLICT (409)
  → REPLACE → LINK/UNLINK с замером времени доезда до DWH и историей версий.
- `verify` — сверка PostgreSQL ↔ ClickHouse ↔ RustFS (строки по каждой
  таблице, количество объектов, байты) плюс состояние пайплайна.

Принципы данных (по требованию):

- никаких выдуманных данных: бэкфилл «исторических» событий удалён, каждое
  событие телеметрии соответствует реальному запросу к API или RustFS;
- `duration_seconds` трека равен реальной длине синтезированного WAV,
  rendition — даунсэмпл того же сигнала;
- `listened_ms` и позиции плеера считаются из реально полученных байт;
- скачивания аудио пользователями нет (права у платформы): событие
  `track_download`, «лицензии» и полное скачивание файла удалены, клиент
  только стримит Range-запросами. Событие `favorite_add` удалено, потому что
  такой функции в системе нет.

### Консоль (`simulator/tui.py`, временная)

`python -m simulator tui` — обычная интерактивная консоль (questionary + rich,
dev-зависимости), без полноэкранного интерфейса: меню → выбор → вывод → меню.

- треки: поиск по списку, карточка, медиа, текст песни;
- добавление трека (пошагово) и пользователя;
- нагрузка: лог действий слушателей и студии в реальном времени, Enter —
  остановка (~4 с), прерванное воспроизведение уходит как `play_skip` с
  реально прослушанным временем, в конце печатается итог;
- генерация, сверка, очистка всех хранилищ (RustFS → PostgreSQL → ожидание
  CDC → TRUNCATE ClickHouse) с подтверждением, очистка с повторной генерацией.

Пользовательские действия идут только через HTTP API приложения, поэтому
консоль можно удалить без последствий. Прямой доступ к хранилищам есть
только у ops-операций (`simulator/maintenance.py`, `simulator/verify.py`).
Клиент симулятора держит keep-alive 2 с (у uvicorn 5 с), иначе после пауз
пользователя возникал `ReadError` на закрытом сервером соединении.

### Веб-интерфейсы для просмотра данных

| Хранилище | URL | Вход |
|---|---|---|
| PostgreSQL | http://127.0.0.1:8081 (pgweb, read-only) | без логина, только localhost |
| RustFS | http://127.0.0.1:9001/rustfs/console/ | `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY` (`chime-admin` / из `.env`) |
| ClickHouse | http://127.0.0.1:8123/play, `/dashboard` | `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD` |
| Redpanda | http://127.0.0.1:8088 | без логина |
| Grafana | http://127.0.0.1:3001 | `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` |

В `.env` рядом с каждой группой переменных указан URL консоли.

### Grafana (`infra/grafana`)

Источник данных и три дашборда описаны кодом (provisioning):
**DWH overview**, **Product analytics**, **API access log**. Все 46 запросов
панелей проверены через `/api/ds/query`.

### Тесты

- `tests/test_api_events.py` — 5 тестов: обогащение и ключ партиции,
  валидация батчей, `503` при недоступной шине, восстановление шаблона
  маршрута, middleware access-лога;
- `tests/test_api_users.py` — 2 теста: жизненный цикл, дубль `409`,
  фильтры, валидация;
- `tests/conftest.py` теперь очищает и `users`.

Итого 42 теста, все проходят.

## Результаты проверки

- Чистый бутстрап с нуля (`docker compose down -v` → up) — успешно.
- `verify`: все 7 таблиц каталога совпадают PG = ClickHouse, объекты и байты
  RustFS совпадают с `storage_objects`.
- Лаг CDC (коммит в PG → строка в ClickHouse): ~1–2 с, p95 ≈ 1.8 с.
- Сверка телеметрии с фактом: число `play_start` = числу редиректов на стрим
  в access-логе, суммарное `listened_ms` совпадает со статистикой клиента.
- `ingest_errors` = 0.

## Бэклог / открытые вопросы

1. **`GET /api/v1/media/{id}/download`** по-прежнему отдаёт 307 на presigned
   URL. По концепции «права у платформы» эта ссылка позволяет скачать файл
   целиком. Варианты: проксировать стриминг через API с Range и
   короткоживущими токенами, отдавать только renditions, сегментированный
   стриминг (HLS) вместо файла. Нужно решение.
2. Бакет создаётся только при старте API. Тесты на свежем RustFS падают,
   пока API ни разу не запускался. Стоит вынести `ensure_bucket` в фикстуру
   тестов или в init-контейнер.
3. Порядок бутстрапа: миграции Alembic должны пройти до `connect-init`
   (коннектор создаёт publication на существующие таблицы). Можно добавить
   контейнер миграций в compose.
4. Тесты очищают общую БД. Нужна отдельная БД или схема для тестов.
5. Замена аудио — по-прежнему DELETE → POST (409 на занятый слот).
6. Дедупликация телеметрии по `event_id` происходит при слияниях
   `ReplacingMergeTree`. Агрегаты `agg_track_daily` теоретически могут
   учесть ретрай дважды. Для точных отчётов — `FINAL` или `uniqExact(event_id)`.
7. Удаление в CDC приходит только с PK (`REPLICA IDENTITY DEFAULT`). Для
   аудита удалённого содержимого — `REPLICA IDENTITY FULL` на нужных таблицах.
8. Single-node Redpanda и ClickHouse (replication factor 1) — только для
   разработки. Для прода: кластер, `ReplicatedMergeTree`, больше партиций и
   `kafka_num_consumers`.
9. `users` API минимальный: нет аутентификации и удаления пользователя.
10. DELETE-эндпоинтов у каталога (артисты, релизы, треки) нет. Удаления в
    демо — это медиа и связи трек ↔ исполнитель.
11. Grafana использует локальную учётку администратора из `.env`. Для общего
    стенда нужны SSO или отдельные viewer-роли.

## Как запустить

```bash
docker compose up -d --wait postgres storage
uv run alembic upgrade head
docker compose up -d
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
uv run python -m simulator seed
uv run python -m simulator run --duration 300 --listeners 15
uv run python -m simulator demo
uv run python -m simulator verify
uv run python -m simulator tui
```

Grafana: http://127.0.0.1:3001 (логин и пароль из `.env`), Redpanda
Console: http://127.0.0.1:8088.
