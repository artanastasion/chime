CREATE OR REPLACE VIEW v_users AS
SELECT * EXCEPT (_version, _is_deleted, _synced_at)
FROM dim_users FINAL
WHERE _is_deleted = 0;

CREATE OR REPLACE VIEW v_artists AS
SELECT * EXCEPT (_version, _is_deleted, _synced_at)
FROM dim_artists FINAL
WHERE _is_deleted = 0;

CREATE OR REPLACE VIEW v_albums AS
SELECT * EXCEPT (_version, _is_deleted, _synced_at)
FROM dim_albums FINAL
WHERE _is_deleted = 0;

CREATE OR REPLACE VIEW v_tracks AS
SELECT * EXCEPT (_version, _is_deleted, _synced_at)
FROM dim_tracks FINAL
WHERE _is_deleted = 0;

CREATE OR REPLACE VIEW v_track_artists AS
SELECT * EXCEPT (_version, _is_deleted, _synced_at)
FROM dim_track_artists FINAL
WHERE _is_deleted = 0;

CREATE OR REPLACE VIEW v_storage_objects AS
SELECT * EXCEPT (_version, _is_deleted, _synced_at)
FROM dim_storage_objects FINAL
WHERE _is_deleted = 0;

CREATE OR REPLACE VIEW v_media_assets AS
SELECT * EXCEPT (_version, _is_deleted, _synced_at)
FROM dim_media_assets FINAL
WHERE _is_deleted = 0;

CREATE OR REPLACE VIEW v_track_primary_artist AS
SELECT
    ta.track_id AS track_id,
    argMin(ar.artist_id, ifNull(ta.display_order, 2147483647)) AS artist_id,
    argMin(ar.name, ifNull(ta.display_order, 2147483647)) AS artist_name
FROM v_track_artists AS ta
INNER JOIN v_artists AS ar ON ar.artist_id = ta.artist_id
WHERE ta.role = 'primary'
GROUP BY ta.track_id;

CREATE OR REPLACE VIEW v_track_catalog AS
SELECT
    t.track_id AS track_id,
    t.title AS title,
    t.status AS status,
    t.explicit_content AS explicit_content,
    t.duration_seconds AS duration_seconds,
    t.album_id AS album_id,
    al.title AS album_title,
    al.album_type AS album_type,
    pa.artist_id AS primary_artist_id,
    pa.artist_name AS primary_artist,
    m.audio_assets AS audio_assets,
    m.media_assets AS media_assets,
    m.media_bytes AS media_bytes,
    t.created_at AS created_at,
    t.updated_at AS updated_at
FROM v_tracks AS t
LEFT JOIN v_albums AS al ON al.album_id = t.album_id
LEFT JOIN v_track_primary_artist AS pa ON pa.track_id = t.track_id
LEFT JOIN
(
    SELECT
        ma.track_id AS track_id,
        countIf(ma.asset_type = 'audio') AS audio_assets,
        count() AS media_assets,
        sum(so.size_bytes) AS media_bytes
    FROM v_media_assets AS ma
    INNER JOIN v_storage_objects AS so ON so.object_id = ma.object_id
    WHERE ma.track_id IS NOT NULL
    GROUP BY ma.track_id
) AS m ON m.track_id = t.track_id;
