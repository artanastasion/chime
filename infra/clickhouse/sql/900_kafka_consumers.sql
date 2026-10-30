CREATE TABLE IF NOT EXISTS cdc_queue
(
    payload String
)
ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'chime.public.users,chime.public.artists,chime.public.albums,chime.public.tracks,chime.public.track_artists,chime.public.storage_objects,chime.public.media_assets',
    kafka_group_name = 'chime_dwh_cdc',
    kafka_format = 'JSONAsString',
    kafka_num_consumers = 1,
    kafka_flush_interval_ms = 1000,
    kafka_handle_error_mode = 'stream';

CREATE MATERIALIZED VIEW IF NOT EXISTS cdc_queue_mv TO cdc_changelog AS
SELECT
    splitByChar('.', _topic)[3] AS source_table,
    JSONExtractString(payload, '__op') AS op,
    JSONExtractUInt(payload, '__lsn') AS lsn,
    fromUnixTimestamp64Milli(JSONExtractInt(payload, '__source_ts_ms'), 'UTC') AS source_ts,
    now64(3) AS ingested_at,
    _partition AS kafka_partition,
    _offset AS kafka_offset,
    payload
FROM cdc_queue
WHERE length(_error) = 0 AND JSONHas(payload, '__op');

CREATE MATERIALIZED VIEW IF NOT EXISTS cdc_queue_errors_mv TO ingest_errors AS
SELECT
    'cdc' AS source,
    _topic AS topic,
    _partition AS kafka_partition,
    _offset AS kafka_offset,
    _error AS error,
    _raw_message AS raw_message,
    now64(3) AS ingested_at
FROM cdc_queue
WHERE length(_error) > 0;

CREATE TABLE IF NOT EXISTS api_access_log_queue
(
    ts             String,
    request_id     String,
    method         String,
    route          String,
    path           String,
    status_code    UInt16,
    duration_ms    Float32,
    request_bytes  UInt64,
    response_bytes UInt64,
    device_id      String,
    user_id        Nullable(String),
    platform       String,
    app_version    String,
    user_agent     String
)
ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'chime.api.access-log',
    kafka_group_name = 'chime_dwh_access_log',
    kafka_format = 'JSONEachRow',
    kafka_num_consumers = 1,
    kafka_flush_interval_ms = 1000,
    kafka_handle_error_mode = 'stream';

CREATE MATERIALIZED VIEW IF NOT EXISTS api_access_log_queue_mv TO fact_api_requests AS
SELECT
    parseDateTime64BestEffort(ts, 3, 'UTC') AS ts,
    toUUIDOrZero(request_id) AS request_id,
    method,
    route,
    path,
    status_code,
    duration_ms,
    request_bytes,
    response_bytes,
    device_id,
    toUUIDOrNull(user_id) AS user_id,
    platform,
    app_version,
    user_agent
FROM api_access_log_queue
WHERE length(_error) = 0;

CREATE MATERIALIZED VIEW IF NOT EXISTS api_access_log_queue_errors_mv TO ingest_errors AS
SELECT
    'api_access_log' AS source,
    _topic AS topic,
    _partition AS kafka_partition,
    _offset AS kafka_offset,
    _error AS error,
    _raw_message AS raw_message,
    now64(3) AS ingested_at
FROM api_access_log_queue
WHERE length(_error) > 0;

CREATE TABLE IF NOT EXISTS app_events_queue
(
    event_id    String,
    event_type  String,
    occurred_at String,
    received_at String,
    session_id  String,
    user_id     Nullable(String),
    device_id   String,
    platform    String,
    app_version String,
    country     String,
    track_id    Nullable(String),
    album_id    Nullable(String),
    position_ms Nullable(UInt32),
    duration_ms Nullable(UInt32),
    properties  Map(String, String)
)
ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'redpanda:9092',
    kafka_topic_list = 'chime.app.events',
    kafka_group_name = 'chime_dwh_app_events',
    kafka_format = 'JSONEachRow',
    kafka_num_consumers = 1,
    kafka_flush_interval_ms = 1000,
    kafka_handle_error_mode = 'stream';

CREATE MATERIALIZED VIEW IF NOT EXISTS app_events_queue_mv TO fact_app_events AS
SELECT
    toUUID(event_id) AS event_id,
    event_type,
    parseDateTime64BestEffort(occurred_at, 3, 'UTC') AS occurred_at,
    parseDateTime64BestEffort(received_at, 3, 'UTC') AS received_at,
    toUUID(session_id) AS session_id,
    toUUIDOrNull(user_id) AS user_id,
    device_id,
    platform,
    app_version,
    country,
    toUUIDOrNull(track_id) AS track_id,
    toUUIDOrNull(album_id) AS album_id,
    position_ms,
    duration_ms,
    properties
FROM app_events_queue
WHERE length(_error) = 0;

CREATE MATERIALIZED VIEW IF NOT EXISTS app_events_queue_errors_mv TO ingest_errors AS
SELECT
    'app_events' AS source,
    _topic AS topic,
    _partition AS kafka_partition,
    _offset AS kafka_offset,
    _error AS error,
    _raw_message AS raw_message,
    now64(3) AS ingested_at
FROM app_events_queue
WHERE length(_error) > 0;
