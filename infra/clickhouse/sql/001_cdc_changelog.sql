CREATE TABLE IF NOT EXISTS cdc_changelog
(
    source_table    LowCardinality(String),
    op              Enum8('c' = 1, 'u' = 2, 'd' = 3, 'r' = 4),
    lsn             UInt64,
    source_ts       DateTime64(3, 'UTC'),
    ingested_at     DateTime64(3, 'UTC') DEFAULT now64(3),
    kafka_partition UInt32,
    kafka_offset    UInt64,
    payload         String CODEC(ZSTD(3))
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(source_ts)
ORDER BY (source_table, source_ts, lsn)
TTL toDateTime(source_ts) + INTERVAL 180 DAY;

CREATE TABLE IF NOT EXISTS ingest_errors
(
    source          LowCardinality(String),
    topic           LowCardinality(String),
    kafka_partition UInt32,
    kafka_offset    UInt64,
    error           String,
    raw_message     String CODEC(ZSTD(3)),
    ingested_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ingested_at)
ORDER BY (source, ingested_at)
TTL toDateTime(ingested_at) + INTERVAL 30 DAY;
