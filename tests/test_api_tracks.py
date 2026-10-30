"""API integration tests: /api/v1/tracks and track-artist links."""

from __future__ import annotations

import uuid

from tests.conftest import Api


def test_create_track_and_validation():
    api = Api()

    async def scenario(client):
        seed = await api.seed_track()
        resp = await client.post(
            "/api/v1/tracks",
            json={
                "title": "Karma Police",
                "duration_seconds": 260,
                "album_id": str(seed["album_id"]),
                "track_number": 3,
                "explicit_content": True,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["title"] == "Karma Police"
        assert body["duration_seconds"] == 260
        assert body["album_id"] == str(seed["album_id"])
        assert body["explicit_content"] is True
        assert body["status"] == "draft"

        for payload in (
            {"title": "X"},  # duration missing
            {"title": "X", "duration_seconds": 0},  # CHECK mirror
            {"title": "X", "duration_seconds": -5},
            {"title": "X", "duration_seconds": 10, "track_number": 0},
            {"title": "X", "duration_seconds": 10, "status": "bogus"},
            {"title": "X", "duration_seconds": 10, "extra": 1},
        ):
            resp = await client.post("/api/v1/tracks", json=payload)
            assert resp.status_code == 422, (payload, resp.text)

        # body reference to a missing album -> 422 (unified contract)
        resp = await client.post(
            "/api/v1/tracks",
            json={
                "title": "X",
                "duration_seconds": 10,
                "album_id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 422
        assert "album" in resp.json()["detail"]

    api.run(scenario)


def test_track_list_filters_and_detail():
    api = Api()

    async def scenario(client):
        seed = await api.seed_track()
        other_album = (
            await client.post("/api/v1/albums", json={"title": "Other"})
        ).json()

        await client.post(
            "/api/v1/tracks",
            json={
                "title": "Alpha Song",
                "duration_seconds": 100,
                "album_id": str(seed["album_id"]),
                "explicit_content": True,
            },
        )
        t2 = (
            await client.post(
                "/api/v1/tracks",
                json={
                    "title": "Beta Song",
                    "duration_seconds": 200,
                    "album_id": other_album["album_id"],
                    "status": "published",
                },
            )
        ).json()

        # link the seed artist to both tracks
        for tid in (seed["track_id"], t2["track_id"]):
            resp = await client.post(
                f"/api/v1/tracks/{tid}/artists",
                json={"artist_id": str(seed["artist_id"]), "role": "primary"},
            )
            assert resp.status_code == 201

        resp = await client.get("/api/v1/tracks")
        assert resp.json()["total"] == 3

        resp = await client.get(
            "/api/v1/tracks", params={"album_id": other_album["album_id"]}
        )
        body = resp.json()
        assert body["total"] == 1 and body["items"][0]["title"] == "Beta Song"

        resp = await client.get("/api/v1/tracks", params={"status": "published"})
        assert resp.json()["total"] == 1

        resp = await client.get("/api/v1/tracks", params={"explicit": "true"})
        assert resp.json()["total"] == 1

        resp = await client.get(
            "/api/v1/tracks", params={"title_icontains": "ALPHA"}
        )
        assert resp.json()["total"] == 1

        # artist filter: distinct rows, one per track (2 of 3 linked)
        resp = await client.get(
            "/api/v1/tracks", params={"artist_id": str(seed["artist_id"])}
        )
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

        # detail: album brief + one link entry per (artist, role)
        await client.post(
            f"/api/v1/tracks/{t2['track_id']}/artists",
            json={"artist_id": str(seed["artist_id"]), "role": "producer"},
        )
        resp = await client.get(f"/api/v1/tracks/{t2['track_id']}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["album"]["title"] == "Other"
        assert len(detail["artists"]) == 2
        assert {a["role"] for a in detail["artists"]} == {"primary", "producer"}

        resp = await client.get(f"/api/v1/tracks/{uuid.uuid4()}")
        assert resp.status_code == 404

    api.run(scenario)


def test_patch_track_including_album_detach():
    api = Api()

    async def scenario(client):
        seed = await api.seed_track()

        resp = await client.patch(
            f"/api/v1/tracks/{seed['track_id']}",
            json={"duration_seconds": 333, "status": "published"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["duration_seconds"] == 333
        assert body["status"] == "published"
        assert body["album_id"] == str(seed["album_id"])  # untouched

        # explicit null detaches the track from its album
        resp = await client.patch(
            f"/api/v1/tracks/{seed['track_id']}", json={"album_id": None}
        )
        assert resp.status_code == 200
        assert resp.json()["album_id"] is None

        # re-attach, then reference a missing album -> 422
        resp = await client.patch(
            f"/api/v1/tracks/{seed['track_id']}",
            json={"album_id": str(seed["album_id"])},
        )
        assert resp.status_code == 200
        resp = await client.patch(
            f"/api/v1/tracks/{seed['track_id']}",
            json={"album_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 422

        resp = await client.patch(
            f"/api/v1/tracks/{uuid.uuid4()}", json={"title": "X"}
        )
        assert resp.status_code == 404

    api.run(scenario)


def test_track_artist_links_crud():
    api = Api()

    async def scenario(client):
        seed = await api.seed_track()
        track_id = seed["track_id"]
        artist_id = seed["artist_id"]

        resp = await client.post(
            f"/api/v1/tracks/{track_id}/artists",
            json={
                "artist_id": str(artist_id),
                "role": "primary",
                "display_order": 2,
            },
        )
        assert resp.status_code == 201, resp.text
        link = resp.json()
        assert link["role"] == "primary" and link["display_order"] == 2
        link_id = link["track_artist_id"]

        # same (track, artist, role) -> 409
        resp = await client.post(
            f"/api/v1/tracks/{track_id}/artists",
            json={"artist_id": str(artist_id), "role": "primary"},
        )
        assert resp.status_code == 409

        # another role for the same artist is fine
        resp = await client.post(
            f"/api/v1/tracks/{track_id}/artists",
            json={"artist_id": str(artist_id), "role": "featured"},
        )
        assert resp.status_code == 201
        second_link = resp.json()["track_artist_id"]

        # body reference to a missing artist -> 422
        resp = await client.post(
            f"/api/v1/tracks/{track_id}/artists",
            json={"artist_id": str(uuid.uuid4()), "role": "primary"},
        )
        assert resp.status_code == 422
        assert "artist" in resp.json()["detail"]

        # missing track in path -> 404
        resp = await client.post(
            f"/api/v1/tracks/{uuid.uuid4()}/artists",
            json={"artist_id": str(artist_id), "role": "primary"},
        )
        assert resp.status_code == 404

        # invalid role -> 422
        resp = await client.post(
            f"/api/v1/tracks/{track_id}/artists",
            json={"artist_id": str(artist_id), "role": "vocalist"},
        )
        assert resp.status_code == 422

        # delete one specific link by its unambiguous id
        resp = await client.delete(
            f"/api/v1/tracks/{track_id}/artists/{link_id}"
        )
        assert resp.status_code == 204

        # the second link survives
        resp = await client.get(f"/api/v1/tracks/{track_id}")
        assert [a["role"] for a in resp.json()["artists"]] == ["featured"]

        # deleting the same link again -> 404
        resp = await client.delete(
            f"/api/v1/tracks/{track_id}/artists/{link_id}"
        )
        assert resp.status_code == 404

        # link addressed through a wrong track -> 404
        resp = await client.delete(
            f"/api/v1/tracks/{uuid.uuid4()}/artists/{second_link}"
        )
        assert resp.status_code == 404

        resp = await client.delete(
            f"/api/v1/tracks/{track_id}/artists/{second_link}"
        )
        assert resp.status_code == 204

    api.run(scenario)

