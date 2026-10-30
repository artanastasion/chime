CREATE TABLE IF NOT EXISTS fact_api_requests
(
    ts             DateTime64(3, 'UTC'),
    request_id     UUID,
    method         LowCardinality(String),
    route          LowCardinality(String),
    path           String,
    status_code    UInt16,
    duration_ms    Float32,
    request_bytes  UInt64,
    response_bytes UInt64,
    device_id      String,
    user_id        Nullable(UUID),
    platform       LowCardinality(String),
    app_version    LowCardinality(String),
    user_agent     String CODEC(ZSTD(3))
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (toDate(ts), route, ts)
TTL toDateTime(ts) + INTERVAL 90 DAY;

CREATE TABLE IF NOT EXISTS fact_app_events
(
    event_id    UUID,
    event_type  LowCardinality(String),
    occurred_at DateTime64(3, 'UTC'),
    received_at DateTime64(3, 'UTC'),
    session_id  UUID,
    user_id     Nullable(UUID),
    device_id   String,
    platform    LowCardinality(String),
    app_version LowCardinality(String),
    country     LowCardinality(String),
    track_id    Nullable(UUID),
    album_id    Nullable(UUID),
    position_ms Nullable(UInt32),
    duration_ms Nullable(UInt32),
    properties  Map(LowCardinality(String), String)
)
ENGINE = ReplacingMergeTree(received_at)
PARTITION BY toYYYYMM(occurred_at)
ORDER BY (toDate(occurred_at), event_type, event_id)
TTL toDateTime(occurred_at) + INTERVAL 365 DAY;

CREATE TABLE IF NOT EXISTS agg_api_minute
(
    minute         DateTime('UTC'),
    route          LowCardinality(String),
    method         LowCardinality(String),
    status_code    UInt16,
    requests       SimpleAggregateFunction(sum, UInt64),
    response_bytes SimpleAggregateFunction(sum, UInt64),
    duration_ms    AggregateFunction(quantilesTDigest(0.5, 0.95, 0.99), Float32)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(minute)
ORDER BY (minute, route, method, status_code)
TTL minute + INTERVAL 180 DAY;

CREATE MATERIALIZED VIEW IF NOT EXISTS agg_api_minute_mv TO agg_api_minute AS
SELECT
    toStartOfMinute(ts) AS minute,
    route,
    method,
    status_code,
    count() AS requests,
    sum(response_bytes) AS response_bytes,
    quantilesTDigestState(0.5, 0.95, 0.99)(duration_ms) AS duration_ms
FROM fact_api_requests
GROUP BY minute, route, method, status_code;

CREATE TABLE IF NOT EXISTS agg_track_daily
(
    day         Date,
    track_id    UUID,
    views       SimpleAggregateFunction(sum, UInt64),
    plays       SimpleAggregateFunction(sum, UInt64),
    completes   SimpleAggregateFunction(sum, UInt64),
    skips       SimpleAggregateFunction(sum, UInt64),
    listened_ms SimpleAggregateFunction(sum, UInt64),
    listeners   AggregateFunction(uniq, String)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(day)
ORDER BY (day, track_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS agg_track_daily_mv TO agg_track_daily AS
SELECT
    toDate(occurred_at) AS day,
    assumeNotNull(track_id) AS track_id,
    countIf(event_type = 'track_view') AS views,
    countIf(event_type = 'play_start') AS plays,
    countIf(event_type = 'play_complete') AS completes,
    countIf(event_type = 'play_skip') AS skips,
    sumIf(toUInt64(ifNull(duration_ms, 0)), event_type IN ('play_complete', 'play_skip')) AS listened_ms,
    uniqStateIf(device_id, event_type = 'play_start') AS listeners
FROM fact_app_events
WHERE track_id IS NOT NULL
GROUP BY day, track_id;
