"""Contract tests for the Esp32Tap full-breadboard builder model."""

import json
from pathlib import Path


MODEL_PATH = Path(__file__).parents[1] / "bringup" / "full-breadboard-model.json"
TOP_LEVEL_KEYS = {
    "schema_version",
    "storage_key",
    "identity",
    "references",
    "tools",
    "parts",
    "nodes",
    "mapping_contracts",
    "nets",
    "items",
    "temporary_configurations",
    "limits",
    "power_states",
    "firmware_roles",
    "phases",
    "steps",
}
PURCHASED_MPNS = {
    "ESP32-S3-DEVKITC-1-N8R8",
    "TPS3700DDCR",
    "TPS70950DBVR",
    "LCQT-SOT23-6",
    "SN74AHC08N",
    "SN74AHC126N",
    "G5V-2 DC5",
    "BC337-40",
    "2N7000",
    "1N5822-TP",
    "P6KE6.8CA",
    "RXEF075",
    "P6KE12A-TP",
    "TSR 1-2433E",
}
STABLE_PART_IDS = {
    "k1",
    "u_tps3700",
    "u_tps709",
    "u_ahc08",
    "u_ahc126",
    "q_relay",
    "q_vbus",
    "j_console",
    "j_motor",
}
REFERENCE_URLS = {
    "g5v2": "https://components.omron.com/sites/default/files/datasheet_pdf/K046-E1.pdf",
    "tps3700": "https://www.ti.com/lit/ds/symlink/tps3700.pdf",
    "tps709": "https://www.ti.com/lit/ds/symlink/tps709.pdf",
    "sn74ahc08": "https://www.ti.com/lit/ds/symlink/sn74ahc08.pdf",
    "sn74ahc126": "https://www.ti.com/lit/ds/symlink/sn74ahc126.pdf",
    "bc337": "https://diotec.com/tl_files/diotec/files/pdf/datasheets/bc337.pdf",
    "2n7000": "https://diotec.com/tl_files/diotec/files/pdf/datasheets/2n7000.pdf",
    "tsr1": "https://www.tracopower.com/model/tsr-1-2433e",
    "p6ke": "https://www.littelfuse.com/products/tvs-diodes/high-power-tvs-diodes/p6ke",
    "rxef": "https://www.littelfuse.com/products/polyswitch-resettable-pptcs/radial-leaded/rxef",
}


def load_model():
    model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    assert set(model) == TOP_LEVEL_KEYS
    return model


def test_model_identity_and_exact_owned_parts():
    model = load_model()
    assert model["schema_version"] == 2
    assert model["storage_key"] == "esp32tap-full-breadboard-builder-v2"

    parts = {part["id"]: part for part in model["parts"]}
    assert len(parts) == len(model["parts"])
    purchased_mpns = {
        part["mpn"] for part in model["parts"] if part["source"] == "purchased"
    }
    assert PURCHASED_MPNS <= purchased_mpns
    assert STABLE_PART_IDS <= set(parts)

    for part_id in ("breadboard", "j_console", "j_motor"):
        assert parts[part_id]["source"] == "operator_mapped"
        assert "pinout" not in parts[part_id]


def test_authoritative_reference_urls_and_retrieval_dates_are_exact():
    references = load_model()["references"]
    assert set(references) == set(REFERENCE_URLS)
    assert {key: value["url"] for key, value in references.items()} == REFERENCE_URLS
    assert {value["retrieved"] for value in references.values()} == {"2026-07-31"}
