from __future__ import annotations

import uuid

from tests.conftest import Api


def test_user_lifecycle():
    async def scenario(client):
        created = await client.post(
            "/api/v1/users", json={"email": " Alice@Example.com "}
        )
        assert created.status_code == 201, created.text
        user = created.json()
        assert user["email"] == "alice@example.com" and user["is_active"] is True

        duplicate = await client.post(
            "/api/v1/users", json={"email": "alice@example.com"}
        )
        assert duplicate.status_code == 409, duplicate.text

        updated = await client.patch(
            f"/api/v1/users/{user['id']}", json={"is_active": False}
        )
        assert updated.status_code == 200 and updated.json()["is_active"] is False

        fetched = await client.get(f"/api/v1/users/{user['id']}")
        assert (
            fetched.status_code == 200
            and fetched.json()["email"] == "alice@example.com"
        )

        missing = await client.get(f"/api/v1/users/{uuid.uuid4()}")
        assert missing.status_code == 404

    Api().run(scenario)


def test_list_users_filters_and_validation():
    async def scenario(client):
        for email, active in (("a@x.io", True), ("b@x.io", False), ("c@y.io", True)):
            resp = await client.post(
                "/api/v1/users", json={"email": email, "is_active": active}
            )
            assert resp.status_code == 201, resp.text

        active = (
            await client.get("/api/v1/users", params={"is_active": "true"})
        ).json()
        assert active["total"] == 2
        by_domain = (
            await client.get("/api/v1/users", params={"email_icontains": "x.io"})
        ).json()
        assert {u["email"] for u in by_domain["items"]} == {"a@x.io", "b@x.io"}

        for payload in (
            {},
            {"email": "not-an-email"},
            {"email": "a@b.c", "role": "admin"},
        ):
            resp = await client.post("/api/v1/users", json=payload)
            assert resp.status_code == 422, (payload, resp.text)

    Api().run(scenario)
