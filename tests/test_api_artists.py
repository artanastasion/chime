"""API integration tests: /api/v1/artists."""

from __future__ import annotations

import uuid

from tests.conftest import Api


def test_create_artist():
    async def scenario(client):
        resp = await client.post("/api/v1/artists", json={"name": "Radiohead"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Radiohead"
        assert body["status"] == "draft"
        assert body["description"] is None
        assert uuid.UUID(body["artist_id"])
        assert body["created_at"]

    Api().run(scenario)


def test_create_artist_validation_errors():
    async def scenario(client):
        for payload in (
            {},  # no name
            {"name": ""},  # empty name
            {"name": "x" * 301},  # too long
            {"name": "X", "status": "bogus"},  # bad enum
            {"name": "X", "extra": 1},  # unknown field (extra=forbid)
        ):
            resp = await client.post("/api/v1/artists", json=payload)
            assert resp.status_code == 422, (payload, resp.text)

    Api().run(scenario)


def test_list_artists_pagination_and_filters():
    async def scenario(client):
        for i in range(5):
            resp = await client.post(
                "/api/v1/artists",
                json={"name": f"Artist {i}", "status": "published"},
            )
            assert resp.status_code == 201
        await client.post("/api/v1/artists", json={"name": "lonely draft"})

        resp = await client.get("/api/v1/artists")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 6
        assert body["limit"] == 20 and body["offset"] == 0
        assert len(body["items"]) == 6

        resp = await client.get(
            "/api/v1/artists", params={"limit": 2, "offset": 2}
        )
        body = resp.json()
        assert len(body["items"]) == 2 and body["total"] == 6

        resp = await client.get("/api/v1/artists", params={"status": "draft"})
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "lonely draft"

        resp = await client.get(
            "/api/v1/artists", params={"name_icontains": "ARTIST 3"}
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "Artist 3"

        # LIKE wildcards in user input must be matched literally
        resp = await client.get(
            "/api/v1/artists", params={"name_icontains": "%ARTIST%"}
        )
        assert resp.json()["total"] == 0

        for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
            resp = await client.get("/api/v1/artists", params=bad)
            assert resp.status_code == 422, bad

    Api().run(scenario)


def test_get_and_patch_artist():
    async def scenario(client):
        created = (
            await client.post("/api/v1/artists", json={"name": "Old Name"})
        ).json()

        resp = await client.get(f"/api/v1/artists/{created['artist_id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Old Name"

        resp = await client.patch(
            f"/api/v1/artists/{created['artist_id']}",
            json={"name": "New Name", "status": "published"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "New Name"
        assert body["status"] == "published"
        assert body["description"] is None  # untouched field

        resp = await client.get(f"/api/v1/artists/{uuid.uuid4()}")
        assert resp.status_code == 404

        resp = await client.patch(
            f"/api/v1/artists/{uuid.uuid4()}", json={"name": "X"}
        )
        assert resp.status_code == 404

        resp = await client.patch(
            f"/api/v1/artists/{created['artist_id']}",
            json={"status": "bogus"},
        )
        assert resp.status_code == 422

    Api().run(scenario)

