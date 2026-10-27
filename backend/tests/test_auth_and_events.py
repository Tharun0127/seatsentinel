"""Signup, login, events and the basic seat map."""
import asyncio


async def test_signup_then_login(client):
    signup = await client.post("/auth/signup", json={"email": "Alice@Example.com", "password": "secret123"})
    assert signup.status_code == 201
    assert signup.json()["email"] == "alice@example.com"

    login = await client.post("/auth/login", json={"email": "alice@example.com", "password": "secret123"})
    assert login.status_code == 200 and login.json()["access_token"]


async def test_wrong_password_is_rejected(client):
    await client.post("/auth/signup", json={"email": "bob@example.com", "password": "secret123"})
    response = await client.post("/auth/login", json={"email": "bob@example.com", "password": "wrong-pass"})
    assert response.status_code == 401


async def test_simultaneous_duplicate_signups_create_one_user(client, db):
    body = {"email": "race@example.com", "password": "secret123"}
    responses = await asyncio.gather(*(client.post("/auth/signup", json=body) for _ in range(10)))
    assert sorted(r.status_code for r in responses) == [201] + [409] * 9
    assert await db.fetchval("SELECT count(*) FROM users WHERE email = 'race@example.com'") == 1


async def test_events_are_listed_with_seat_counts(client):
    events = (await client.get("/events")).json()
    assert len(events) == 1
    assert events[0]["seats_total"] == 200 and events[0]["seats_booked"] == 0


async def test_seat_map_has_rows_a_to_j_with_20_seats(client):
    seats = (await client.get("/events/1/seats")).json()
    assert len(seats) == 200
    assert {s["row_label"] for s in seats} == set("ABCDEFGHIJ")
    assert all(s["state"] == "free" for s in seats)


async def test_unknown_event_is_404(client):
    assert (await client.get("/events/999/seats")).status_code == 404
