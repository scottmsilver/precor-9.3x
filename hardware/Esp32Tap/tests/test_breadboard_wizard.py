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
EXPECTED_PARTS = {
    "devkit": ("ESP32-S3-DEVKITC-1-N8R8", "purchased", 1, None),
    "u_tps3700": ("TPS3700DDCR", "purchased", 1, "tps3700"),
    "adapter_tps3700": ("LCQT-SOT23-6", "purchased", 1, None),
    "u_tps709": ("TPS70950DBVR", "purchased", 1, "tps709"),
    "adapter_tps709": ("LCQT-SOT23-6", "purchased", 1, None),
    "u_ahc08": ("SN74AHC08N", "purchased", 1, "sn74ahc08"),
    "u_ahc126": ("SN74AHC126N", "purchased", 1, "sn74ahc126"),
    "k1": ("G5V-2 DC5", "purchased", 1, "g5v2"),
    "q_relay": ("BC337-40", "purchased", 1, "bc337"),
    "q_vbus": ("2N7000", "purchased", 1, "2n7000"),
    "d_input": ("1N5822-TP", "purchased", 1, None),
    "d_coil_tvs": ("P6KE6.8CA", "purchased", 1, "p6ke"),
    "f_input": ("RXEF075", "purchased", 1, "rxef"),
    "d_input_tvs": ("P6KE12A-TP", "purchased", 1, "p6ke"),
    "u_tsr": ("TSR 1-2433E", "purchased", 1, "tsr1"),
    "breadboard": (None, "operator_mapped", 1, None),
    "j_console": (None, "operator_mapped", 1, None),
    "j_motor": (None, "operator_mapped", 1, None),
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
    assert model["identity"]["name"] == "Esp32Tap full-breadboard relay builder"
    assert model["identity"]["architecture"] == "full_breadboard"

    actual_parts = {
        part["id"]: (
            part["mpn"],
            part["source"],
            part["quantity"],
            part.get("reference"),
        )
        for part in model["parts"]
    }
    assert len(actual_parts) == len(model["parts"])
    assert actual_parts == EXPECTED_PARTS

    for part_id in ("breadboard", "j_console", "j_motor"):
        part = next(part for part in model["parts"] if part["id"] == part_id)
        assert "pinout" not in part


def test_authoritative_reference_urls_and_retrieval_dates_are_exact():
    references = load_model()["references"]
    assert set(references) == set(REFERENCE_URLS)
    assert {key: value["url"] for key, value in references.items()} == REFERENCE_URLS
    assert {value["retrieved"] for value in references.values()} == {"2026-07-31"}


def test_later_task_sections_start_with_exact_empty_container_types():
    model = load_model()
    for section in (
        "nodes",
        "mapping_contracts",
        "nets",
        "temporary_configurations",
        "limits",
        "power_states",
        "firmware_roles",
    ):
        assert model[section] == {}
        assert type(model[section]) is dict

    for section in ("tools", "items", "phases", "steps"):
        assert model[section] == []
        assert type(model[section]) is list
