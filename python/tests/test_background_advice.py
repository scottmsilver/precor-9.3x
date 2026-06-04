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
