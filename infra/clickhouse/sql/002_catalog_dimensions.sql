CREATE TABLE IF NOT EXISTS dim_users
(
    user_id     UUID,
    email       String,
    is_active   Bool,
    created_at  DateTime64(6, 'UTC'),
    updated_at  DateTime64(6, 'UTC'),
    _version    UInt64,
    _is_deleted UInt8,
    _synced_at  DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(_version, _is_deleted)
ORDER BY user_id;

CREATE MATERIALIZED VIEW IF NOT EXISTS dim_users_mv TO dim_users AS
SELECT
    toUUID(JSONExtractString(payload, 'id')) AS user_id,
    JSONExtractString(payload, 'email') AS email,
    JSONExtractBool(payload, 'is_active') AS is_active,
    parseDateTime64BestEffortOrZero(JSONExtractString(payload, 'created_at'), 6, 'UTC') AS created_at,
    parseDateTime64BestEffortOrZero(JSONExtractString(payload, 'updated_at'), 6, 'UTC') AS updated_at,
    lsn AS _version,
    op = 'd' AS _is_deleted,
    ingested_at AS _synced_at
FROM cdc_changelog
WHERE source_table = 'users';

CREATE TABLE IF NOT EXISTS dim_artists
(
    artist_id   UUID,
    name        String,
    description Nullable(String),
    status      LowCardinality(String),
    created_at  DateTime64(6, 'UTC'),
    _version    UInt64,
    _is_deleted UInt8,
    _synced_at  DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(_version, _is_deleted)
ORDER BY artist_id;

CREATE MATERIALIZED VIEW IF NOT EXISTS dim_artists_mv TO dim_artists AS
SELECT
    toUUID(JSONExtractString(payload, 'artist_id')) AS artist_id,
    JSONExtractString(payload, 'name') AS name,
    JSONExtract(payload, 'description', 'Nullable(String)') AS description,
    JSONExtractString(payload, 'status') AS status,
    parseDateTime64BestEffortOrZero(JSONExtractString(payload, 'created_at'), 6, 'UTC') AS created_at,
    lsn AS _version,
    op = 'd' AS _is_deleted,
    ingested_at AS _synced_at
FROM cdc_changelog
WHERE source_table = 'artists';

CREATE TABLE IF NOT EXISTS dim_albums
(
    album_id     UUID,
    title        String,
    release_date Nullable(Date),
    album_type   LowCardinality(String),
    status       LowCardinality(String),
    created_at   DateTime64(6, 'UTC'),
    updated_at   DateTime64(6, 'UTC'),
    _version     UInt64,
    _is_deleted  UInt8,
    _synced_at   DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(_version, _is_deleted)
ORDER BY album_id;

CREATE MATERIALIZED VIEW IF NOT EXISTS dim_albums_mv TO dim_albums AS
SELECT
    toUUID(JSONExtractString(payload, 'album_id')) AS album_id,
    JSONExtractString(payload, 'title') AS title,
    if(
        JSONType(payload, 'release_date') = 'Int64',
        addDays(toDate('1970-01-01'), JSONExtractInt(payload, 'release_date')),
        NULL
    ) AS release_date,
    JSONExtractString(payload, 'album_type') AS album_type,
    JSONExtractString(payload, 'status') AS status,
    parseDateTime64BestEffortOrZero(JSONExtractString(payload, 'created_at'), 6, 'UTC') AS created_at,
    parseDateTime64BestEffortOrZero(JSONExtractString(payload, 'updated_at'), 6, 'UTC') AS updated_at,
    lsn AS _version,
    op = 'd' AS _is_deleted,
    ingested_at AS _synced_at
FROM cdc_changelog
WHERE source_table = 'albums';

CREATE TABLE IF NOT EXISTS dim_tracks
(
    track_id         UUID,
    album_id         Nullable(UUID),
    title            String,
    duration_seconds UInt32,
    track_number     Nullable(UInt16),
    explicit_content Bool,
    status           LowCardinality(String),
    created_at       DateTime64(6, 'UTC'),
    updated_at       DateTime64(6, 'UTC'),
    _version         UInt64,
    _is_deleted      UInt8,
    _synced_at       DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(_version, _is_deleted)
ORDER BY track_id;

CREATE MATERIALIZED VIEW IF NOT EXISTS dim_tracks_mv TO dim_tracks AS
SELECT
    toUUID(JSONExtractString(payload, 'track_id')) AS track_id,
    toUUIDOrNull(JSONExtractString(payload, 'album_id')) AS album_id,
    JSONExtractString(payload, 'title') AS title,
    JSONExtractUInt(payload, 'duration_seconds') AS duration_seconds,
    JSONExtract(payload, 'track_number', 'Nullable(UInt16)') AS track_number,
    JSONExtractBool(payload, 'explicit_content') AS explicit_content,
    JSONExtractString(payload, 'status') AS status,
    parseDateTime64BestEffortOrZero(JSONExtractString(payload, 'created_at'), 6, 'UTC') AS created_at,
    parseDateTime64BestEffortOrZero(JSONExtractString(payload, 'updated_at'), 6, 'UTC') AS updated_at,
    lsn AS _version,
    op = 'd' AS _is_deleted,
    ingested_at AS _synced_at
FROM cdc_changelog
WHERE source_table = 'tracks';

CREATE TABLE IF NOT EXISTS dim_track_artists
(
    track_artist_id UUID,
    track_id        UUID,
    artist_id       UUID,
    role            LowCardinality(String),
    display_order   Nullable(Int32),
    _version        UInt64,
    _is_deleted     UInt8,
    _synced_at      DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(_version, _is_deleted)
ORDER BY track_artist_id;

CREATE MATERIALIZED VIEW IF NOT EXISTS dim_track_artists_mv TO dim_track_artists AS
SELECT
    toUUID(JSONExtractString(payload, 'track_artist_id')) AS track_artist_id,
    toUUIDOrZero(JSONExtractString(payload, 'track_id')) AS track_id,
    toUUIDOrZero(JSONExtractString(payload, 'artist_id')) AS artist_id,
    JSONExtractString(payload, 'role') AS role,
    JSONExtract(payload, 'display_order', 'Nullable(Int32)') AS display_order,
    lsn AS _version,
    op = 'd' AS _is_deleted,
    ingested_at AS _synced_at
FROM cdc_changelog
WHERE source_table = 'track_artists';

CREATE TABLE IF NOT EXISTS dim_storage_objects
(
    object_id          UUID,
    bucket_name        LowCardinality(String),
    object_key         String,
    mime_type          LowCardinality(String),
    size_bytes         UInt64,
    checksum_algorithm LowCardinality(String),
    checksum           String,
    status             LowCardinality(String),
    created_at         DateTime64(6, 'UTC'),
    _version           UInt64,
    _is_deleted        UInt8,
    _synced_at         DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(_version, _is_deleted)
ORDER BY object_id;

CREATE MATERIALIZED VIEW IF NOT EXISTS dim_storage_objects_mv TO dim_storage_objects AS
SELECT
    toUUID(JSONExtractString(payload, 'object_id')) AS object_id,
    JSONExtractString(payload, 'bucket_name') AS bucket_name,
    JSONExtractString(payload, 'object_key') AS object_key,
    JSONExtractString(payload, 'mime_type') AS mime_type,
    JSONExtractUInt(payload, 'size_bytes') AS size_bytes,
    JSONExtractString(payload, 'checksum_algorithm') AS checksum_algorithm,
    JSONExtractString(payload, 'checksum') AS checksum,
    JSONExtractString(payload, 'status') AS status,
    parseDateTime64BestEffortOrZero(JSONExtractString(payload, 'created_at'), 6, 'UTC') AS created_at,
    lsn AS _version,
    op = 'd' AS _is_deleted,
    ingested_at AS _synced_at
FROM cdc_changelog
WHERE source_table = 'storage_objects';

CREATE TABLE IF NOT EXISTS dim_media_assets
(
    asset_id      UUID,
    track_id      Nullable(UUID),
    album_id      Nullable(UUID),
    object_id     UUID,
    asset_type    LowCardinality(String),
    language      Nullable(String),
    audio_format  LowCardinality(Nullable(String)),
    audio_bitrate Nullable(UInt32),
    is_primary    Bool,
    created_at    DateTime64(6, 'UTC'),
    _version      UInt64,
    _is_deleted   UInt8,
    _synced_at    DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(_version, _is_deleted)
ORDER BY asset_id;

CREATE MATERIALIZED VIEW IF NOT EXISTS dim_media_assets_mv TO dim_media_assets AS
SELECT
    toUUID(JSONExtractString(payload, 'asset_id')) AS asset_id,
    toUUIDOrNull(JSONExtractString(payload, 'track_id')) AS track_id,
    toUUIDOrNull(JSONExtractString(payload, 'album_id')) AS album_id,
    toUUIDOrZero(JSONExtractString(payload, 'object_id')) AS object_id,
    JSONExtractString(payload, 'asset_type') AS asset_type,
    JSONExtract(payload, 'language', 'Nullable(String)') AS language,
    JSONExtract(payload, 'audio_format', 'Nullable(String)') AS audio_format,
    JSONExtract(payload, 'audio_bitrate', 'Nullable(UInt32)') AS audio_bitrate,
    JSONExtractBool(payload, 'is_primary') AS is_primary,
    parseDateTime64BestEffortOrZero(JSONExtractString(payload, 'created_at'), 6, 'UTC') AS created_at,
    lsn AS _version,
    op = 'd' AS _is_deleted,
    ingested_at AS _synced_at
FROM cdc_changelog
WHERE source_table = 'media_assets';
