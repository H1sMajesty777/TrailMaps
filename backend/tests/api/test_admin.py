from tests.helpers import PASSWORD, create_route, login


async def test_user_management_is_admin_only(client, alice, manager, admin):
    assert (await client.get("/api/admin/users")).status_code == 401
    assert (await client.get("/api/admin/users", headers=alice.headers)).status_code == 403
    assert (await client.get("/api/admin/users", headers=manager.headers)).status_code == 403
    r = await client.get("/api/admin/users", headers=admin.headers)
    assert r.status_code == 200 and r.json()["total"] == 3
    item = r.json()["items"][0]
    assert {"id", "email", "phone", "role", "is_active", "last_login"} <= set(item)
    assert "password_hash" not in r.text


async def test_user_list_filters_and_pagination(client, alice, bob, manager, admin):
    by_q = (await client.get("/api/admin/users", params={"q": "ALI"}, headers=admin.headers)).json()
    assert [u["username"] for u in by_q["items"]] == ["alice"]
    by_email = (await client.get("/api/admin/users", params={"q": "bob@"}, headers=admin.headers)).json()
    assert [u["username"] for u in by_email["items"]] == ["bob"]
    wildcard = (await client.get("/api/admin/users", params={"q": "%"}, headers=admin.headers)).json()
    assert wildcard["total"] == 0  # LIKE wildcards are escaped
    by_role = (await client.get("/api/admin/users", params={"role": "manager"}, headers=admin.headers)).json()
    assert [u["username"] for u in by_role["items"]] == ["manny"]
    page = (await client.get("/api/admin/users", params={"limit": 2, "page": 2}, headers=admin.headers)).json()
    assert page["total"] == 4 and len(page["items"]) == 2 and page["pages"] == 2
    assert (await client.get("/api/admin/users", params={"role": "boss"}, headers=admin.headers)).status_code == 422


async def test_block_user_filters_and_effects(client, alice, admin):
    r = await client.put(f"/api/admin/users/{alice.id}", json={"is_active": False}, headers=admin.headers)
    assert r.status_code == 200 and r.json()["is_active"] is False
    blocked = (await client.get("/api/admin/users", params={"is_active": "false"}, headers=admin.headers)).json()
    assert [u["username"] for u in blocked["items"]] == ["alice"]
    login_attempt = await client.post("/api/auth/login", json={"email": alice.email, "password": PASSWORD})
    assert login_attempt.status_code == 403
    assert (await client.get("/api/auth/me", headers=alice.headers)).status_code == 403
    await client.put(f"/api/admin/users/{alice.id}", json={"is_active": True}, headers=admin.headers)
    await login(client, alice.email)


async def test_role_change_applies_immediately(client, alice, admin):
    assert (await client.get("/api/complaints", headers=alice.headers)).status_code == 403
    r = await client.put(f"/api/admin/users/{alice.id}", json={"role": "manager"}, headers=admin.headers)
    assert r.status_code == 200 and r.json()["role"] == "manager"
    assert (await client.get("/api/complaints", headers=alice.headers)).status_code == 200
    await client.put(f"/api/admin/users/{alice.id}", json={"role": "user"}, headers=admin.headers)
    assert (await client.get("/api/complaints", headers=alice.headers)).status_code == 403


async def test_admin_cannot_lock_themselves_out(client, admin):
    for body in ({"role": "user"}, {"is_active": False}):
        r = await client.put(f"/api/admin/users/{admin.id}", json=body, headers=admin.headers)
        assert r.status_code == 409 and r.json()["error"]["code"] == "self_modification"


async def test_update_validation_and_unknown_user(client, alice, admin):
    url = f"/api/admin/users/{alice.id}"
    for body in ({}, {"role": "boss"}, {"role": None}, {"is_active": None}):
        assert (await client.put(url, json=body, headers=admin.headers)).status_code == 422, body
    assert (await client.put("/api/admin/users/99999", json={"role": "user"}, headers=admin.headers)).status_code == 404


async def test_hide_and_unhide_route(client, alice, bob, manager, admin):
    route = await create_route(client, alice)
    url = f"/api/admin/routes/{route['id']}/visibility"
    assert (await client.put(url, json={"is_hidden": True})).status_code == 401
    assert (await client.put(url, json={"is_hidden": True}, headers=manager.headers)).status_code == 403
    r = await client.put(url, json={"is_hidden": True}, headers=admin.headers)
    assert r.status_code == 200 and r.json() == {"id": route["id"], "is_hidden": True}
    assert (await client.get(f"/api/routes/{route['id']}", headers=bob.headers)).status_code == 404
    assert (await client.get(f"/api/routes/{route['id']}", headers=manager.headers)).status_code == 200
    await client.put(url, json={"is_hidden": False}, headers=admin.headers)
    assert (await client.get(f"/api/routes/{route['id']}", headers=bob.headers)).status_code == 200
    assert (await client.put("/api/admin/routes/99999/visibility", json={"is_hidden": True}, headers=admin.headers)).status_code == 404
    assert (await client.put(url, json={}, headers=admin.headers)).status_code == 422
