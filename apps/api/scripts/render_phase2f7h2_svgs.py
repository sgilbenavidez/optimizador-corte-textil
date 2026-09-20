"""Phase 2F.7H-2: render SVGs for the minimum required marker set (Section 22).

Reconstructs each marker's real transformed geometry from the stored
(piece_instance_id, rotation, translation) triples in best-marker.json via
transform_piece -- the same production transform contract
IndependentMarkerValidator itself uses to recompute expected geometry -- so
no separate/invented visualization layout is ever created; this only
re-derives the already-validated placement, it does not re-run any search.

Rendering follows the exact visual convention services.py::marker_svg
already uses in production (polygon points with a y-flip, same colors),
adapted to work directly from in-memory Placement tuples instead of a
persisted MarkerArtifactORM row.
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

from costura_optima.domain.integer_kernel import path_bbox, rotate_and_translate_point, transform_piece
from run_phase2f7h2_composition_search import _SessionFixture, build_request  # noqa: E402


MARKERS = {
    "M1": (("M", 1),),
    "M2": (("M", 2),),
    "M3": (("M", 3),),
    "M4": (("M", 4),),
    "M2+L1": (("M", 2), ("L", 1)),
    "S3+XXL5": (("S", 3), ("XXL", 5)),
}


def render_svg(request, by_id, rows: list[dict]) -> str:
    body: list[str] = []
    max_x = 0
    width = request.usable_width
    for row in rows:
        instance = by_id[row["piece_instance_id"]]
        polygon = transform_piece(instance.piece.cut_polygon, row["rotation"], tuple(row["translation"]))
        bbox = path_bbox(polygon)
        max_x = max(max_x, bbox[2])
        points = " ".join(f"{x},{width - y}" for x, y in polygon)
        title = escape(f"{instance.piece.piece_code} · {instance.piece.size_code} · {row['rotation']}°")
        body.append(f'<g><polygon points="{points}" fill="#dcebe5" stroke="#163b40"><title>{title}</title></polygon></g>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {max_x} {width}" '
           f'role="img" aria-label="Marker 2F.7H-2">'
           f'<rect width="{max_x}" height="{width}" fill="#fffdf7" stroke="#163b40"/>{"".join(body)}</svg>')
    return svg


def main():
    output_dir = Path("artifacts/phase2f7h12/svg")
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture = _SessionFixture()
    for tag, composition in MARKERS.items():
        best_marker_path = Path("artifacts/phase2f7h2") / tag / "best-marker.json"
        if not best_marker_path.exists():
            print(f"[render] skip {tag}: no best-marker.json")
            continue
        rows = json.loads(best_marker_path.read_text(encoding="utf-8"))["placements"]
        request = build_request(fixture, composition)
        by_id = {item.instance_id: item for item in request.piece_instances}
        svg = render_svg(request, by_id, rows)
        target = output_dir / f"{tag}.svg"
        target.write_text(svg, encoding="utf-8")
        print(f"[render] wrote {target}")


if __name__ == "__main__":
    main()
