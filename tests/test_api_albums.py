"""API integration tests: /api/v1/albums."""

from __future__ import annotations

import uuid

from tests.conftest import Api


def test_create_and_get_album():
    async def scenario(client):
        resp = await client.post(
            "/api/v1/albums",
            json={"title": "OK Computer", "album_type": "album"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["title"] == "OK Computer"
        assert body["album_type"] == "album"
        assert body["status"] == "draft"
        assert body["release_date"] is None

        resp = await client.get(f"/api/v1/albums/{body['album_id']}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["tracks"] == [] and detail["artists"] == []

        assert (
            await client.get(f"/api/v1/albums/{uuid.uuid4()}")
        ).status_code == 404

    Api().run(scenario)


def test_create_album_validation():
    async def scenario(client):
        for payload in (
            {},
            {"title": ""},
            {"title": "X", "album_type": "box-set"},
            {"title": "X", "release_date": "not-a-date"},
            {"title": "X", "status": "bogus"},
        ):
            resp = await client.post("/api/v1/albums", json=payload)
            assert resp.status_code == 422, (payload, resp.text)

        resp = await client.post(
            "/api/v1/albums",
            json={"title": "Dated", "release_date": "2024-05-01"},
        )
        assert resp.status_code == 201
        assert resp.json()["release_date"] == "2024-05-01"

    Api().run(scenario)


def test_album_filters_and_patch():
    async def scenario(client):
        ids = {}
        for title, atype, astatus in (
            ("Alpha", "album", "published"),
            ("Beta", "ep", "draft"),
            ("Gamma", "single", "published"),
        ):
            resp = await client.post(
                "/api/v1/albums",
                json={"title": title, "album_type": atype, "status": astatus},
            )
            ids[title] = resp.json()["album_id"]

        resp = await client.get("/api/v1/albums", params={"status": "published"})
        assert resp.json()["total"] == 2

        resp = await client.get("/api/v1/albums", params={"album_type": "ep"})
        assert resp.json()["total"] == 1

        resp = await client.patch(
            f"/api/v1/albums/{ids['Beta']}",
            json={"title": "Beta II", "status": "published"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Beta II" and body["status"] == "published"
        assert body["album_type"] == "ep"  # untouched

        resp = await client.patch(
            f"/api/v1/albums/{uuid.uuid4()}", json={"title": "X"}
        )
        assert resp.status_code == 404

    Api().run(scenario)


def test_album_detail_with_tracks_and_artists():
    api = Api()

    async def scenario(client):
        seed = await api.seed_track()
        track_id = seed["track_id"]

        # add a second track to the album via API
        resp = await client.post(
            "/api/v1/tracks",
            json={
                "title": "Second",
                "duration_seconds": 50,
                "album_id": str(seed["album_id"]),
                "track_number": 1,
            },
        )
        assert resp.status_code == 201, resp.text

        resp = await client.get(f"/api/v1/albums/{seed['album_id']}")
        assert resp.status_code == 200
        detail = resp.json()
        assert len(detail["tracks"]) == 2
        # the track with track_number=1 comes first (nulls last ordering)
        assert detail["tracks"][0]["title"] == "Second"
        # no artists linked yet
        assert detail["artists"] == []

        # link an artist; album detail must show it once even if the
        # artist holds two roles across the album's tracks
        resp = await client.post(
            f"/api/v1/tracks/{track_id}/artists",
            json={"artist_id": str(seed["artist_id"]), "role": "primary"},
        )
        assert resp.status_code == 201
        resp = await client.post(
            f"/api/v1/tracks/{track_id}/artists",
            json={"artist_id": str(seed["artist_id"]), "role": "producer"},
        )
        assert resp.status_code == 201

        resp = await client.get(f"/api/v1/albums/{seed['album_id']}")
        artists = resp.json()["artists"]
        assert len(artists) == 1  # distinct
        assert artists[0]["artist_id"] == str(seed["artist_id"])

        # filter albums by artist (distinct, no duplicates)
        resp = await client.get(
            "/api/v1/albums", params={"artist_id": str(seed["artist_id"])}
        )
        body = resp.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1

    api.run(scenario)
