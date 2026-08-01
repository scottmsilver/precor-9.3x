import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HTML = ROOT / "hardware/Esp32Tap/bringup/esp32tap-breadboard-from-to.html"
PDF = ROOT / "hardware/Esp32Tap/bringup/esp32tap-breadboard-from-to.pdf"
EXPECTED_ORDER = [3, 7, 6, 1, 2, 4, 5]
EXPECTED_PAGE_CLUSTERS = [[3, 7], [6], [1, 2], [4], [5]]
ALLOWED_COLORS = {
    "BLACK",
    "RED",
    "ORANGE",
    "BLUE",
    "VIOLET",
    "YELLOW",
    "GREEN",
    "WHITE",
    "NO WIRE",
}


def metadata() -> dict:
    source = HTML.read_text(encoding="utf-8")
    match = re.search(
        r'<script id="cluster-metadata" type="application/json">\s*(.*?)\s*</script>',
        source,
        re.DOTALL,
    )
    assert match, "cluster-metadata JSON is missing"
    return json.loads(match.group(1))


def test_cluster_plan_covers_every_original_reference_once():
    data = metadata()
    assert data["cluster_order"] == EXPECTED_ORDER
    assert data["page_break_before"] == [6, 1, 4, 5]

    clusters = data["clusters"]
    assert [cluster["number"] for cluster in clusters] == EXPECTED_ORDER
    references = [ref for cluster in clusters for ref in cluster["refs"]]
    assert len(references) == 126
    assert sorted(references) == list(range(1, 127))


def test_every_reference_has_one_valid_wire_color():
    data = metadata()
    assignments = {
        ref: color
        for color, references in data["wire_colors"].items()
        for ref in references
    }
    reference_count = sum(len(references) for references in data["wire_colors"].values())

    assert set(data["wire_colors"]) == ALLOWED_COLORS
    assert reference_count == 126
    assert sorted(assignments) == list(range(1, 127))
    assert assignments[71] == assignments[77] == assignments[98] == "NO WIRE"


def test_pdf_uses_suggested_cluster_order_and_color_annotations():
    text = subprocess.run(
        ["pdftotext", "-layout", str(PDF), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    headings = [f"Build {index} — Cluster {cluster}" for index, cluster in enumerate(EXPECTED_ORDER, 1)]
    positions = [text.index(heading) for heading in headings]
    assert positions == sorted(positions)
    for color in ALLOWED_COLORS:
        assert color in text

    pages = text.split("\f")
    rendered_clusters = []
    for page in pages[:5]:
        rendered_clusters.append(
            [
                cluster
                for cluster in EXPECTED_ORDER
                if re.search(rf"Build \d+ — Cluster {cluster}(?::|\b)", page)
            ]
        )
    assert rendered_clusters == EXPECTED_PAGE_CLUSTERS
