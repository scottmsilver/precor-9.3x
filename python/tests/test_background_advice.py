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
