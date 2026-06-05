import json
from unittest.mock import MagicMock, patch

import program_engine


def test_advise_background_coerces_partial_json():
    fake = MagicMock()
    fake.text = json.dumps({"palette_hue": 158})  # missing other fields
    with patch.object(program_engine, "call_gemini_image", return_value=fake):
        out = program_engine.advise_background(b"\xff\xd8fakejpeg")
    assert out["palette_hue"] == 158
    assert out["suggested_polarity"] in ("light", "dark")  # defaulted
    assert isinstance(out["busy_zones"], list)


def test_advise_background_neutral_on_error():
    with patch.object(program_engine, "call_gemini_image", side_effect=RuntimeError("no key")):
        out = program_engine.advise_background(b"x")
    assert out["suggested_polarity"] == "light"
    assert out["busy_zones"] == []


import base64

from fastapi.testclient import TestClient


def test_endpoint_cache_hit_skips_gemini(monkeypatch):
    import server

    calls = {"n": 0}

    def fake_advise(b):
        calls["n"] += 1
        return {"palette_hue": 120.0, "suggested_polarity": "light", "mood": "x", "busy_zones": []}

    monkeypatch.setattr(server.program_engine, "advise_background", fake_advise)
    server._background_advice_cache.clear()
    client = TestClient(server.app)
    img = base64.b64encode(b"\xff\xd8jpeg").decode()
    body = {"image_hash": "abc", "image_b64": img}
    r1 = client.post("/api/background/advise", json=body)
    r2 = client.post("/api/background/advise", json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["palette_hue"] == 120.0
    assert calls["n"] == 1  # second call served from cache


def test_endpoint_validates_hash():
    import server

    client = TestClient(server.app)
    r = client.post("/api/background/advise", json={})
    assert r.status_code == 422 or r.status_code == 400


def test_endpoint_rejects_oversized_image(monkeypatch):
    import server

    called = {"n": 0}
    monkeypatch.setattr(server.program_engine, "advise_background", lambda b: called.__setitem__("n", 1))
    client = TestClient(server.app)
    huge = "A" * (server.MAX_ADVISE_B64_LEN + 4)  # oversized base64 string
    r = client.post("/api/background/advise", json={"image_hash": "x", "image_b64": huge})
    # Rejected either by pydantic max_length (422) or the pre-decode length check (400);
    # either way Gemini is never called.
    assert r.status_code in (400, 422)
    assert called["n"] == 0


def test_endpoint_keys_cache_by_server_digest_not_client_hash(monkeypatch):
    import server

    monkeypatch.setattr(
        server.program_engine,
        "advise_background",
        lambda b: {"palette_hue": 1.0, "suggested_polarity": "light", "mood": "m", "busy_zones": []},
    )
    server._background_advice_cache.clear()
    client = TestClient(server.app)
    img = base64.b64encode(b"\xff\xd8jpeg").decode()
    # Store advice for these bytes under an attacker-chosen hash.
    client.post("/api/background/advise", json={"image_hash": "attacker", "image_b64": img})
    # A later image-less lookup under the attacker hash must NOT return the stored prior:
    # writes are keyed by the server-computed sha256, not the client string.
    r = client.post("/api/background/advise", json={"image_hash": "attacker"})
    assert r.json()["palette_hue"] is None  # neutral prior, not the poisoned value


def test_endpoint_rejects_malformed_base64(monkeypatch):
    import server

    called = {"n": 0}
    monkeypatch.setattr(server.program_engine, "advise_background", lambda b: called.__setitem__("n", 1))
    client = TestClient(server.app)
    # Within the length cap but not valid base64 (validate=True rejects '!') → 400, no Gemini call.
    r = client.post("/api/background/advise", json={"image_hash": "x", "image_b64": "!!!!notbase64!!!!"})
    assert r.status_code == 400
    assert called["n"] == 0


def test_endpoint_evicts_oldest_past_cap(monkeypatch):
    import hashlib

    import server

    monkeypatch.setattr(
        server.program_engine,
        "advise_background",
        lambda b: {"palette_hue": 1.0, "suggested_polarity": "light", "mood": "m", "busy_zones": []},
    )
    server._background_advice_cache.clear()
    client = TestClient(server.app)
    cap = server.MAX_ADVICE_CACHE_ENTRIES
    for i in range(cap + 5):  # distinct bytes -> distinct server-computed sha256 keys
        img = base64.b64encode(f"img-{i}".encode()).decode()
        r = client.post("/api/background/advise", json={"image_hash": "h", "image_b64": img})
        assert r.status_code == 200
    assert len(server._background_advice_cache) == cap  # bounded
    # The oldest image's digest was evicted (FIFO).
    assert hashlib.sha256(b"img-0").hexdigest() not in server._background_advice_cache


def test_sanitize_busy_zone_drops_unexpected_fields_and_clamps():
    z = program_engine._sanitize_busy_zone(
        {"x": 2.0, "y": -1, "w": 0.5, "h": 0.5, "note": "n" * 200, "evil": "x" * 99999}
    )
    assert z == {"x": 1.0, "y": 0.0, "w": 0.5, "h": 0.5, "note": "n" * 60}
    assert "evil" not in z
