"""Phase 2F.7H-3: render SVGs for every marker used in the best exact
production solution (Section 17). Reuses render_phase2f7h2_svgs.py's
render_svg (same transform_piece reconstruction, same visual convention)
directly -- not duplicated.
"""
from __future__ import annotations

import json
from pathlib import Path

from render_phase2f7h2_svgs import render_svg  # noqa: E402
from run_phase2f7h2_composition_search import _SessionFixture, build_request  # noqa: E402

# The 4 markers in the winning exact-production-216 solution (2 from
# 2F.7H-2's catalog, 2 newly discovered this phase).
WINNING_MARKERS = {
    "S3+XXL5": ((("S", 3), ("XXL", 5)), Path("artifacts/phase2f7h2/S3+XXL5/best-marker.json")),
    "M2+L1": ((("M", 2), ("L", 1)), Path("artifacts/phase2f7h2/M2+L1/best-marker.json")),
    "XS3+XL3": ((("XS", 3), ("XL", 3)), Path("artifacts/phase2f7h3/XS3+XL3/best-marker.json")),
    "XL1": ((("XL", 1),), Path("artifacts/phase2f7h3/XL1/best-marker.json")),
}


def main():
    output_dir = Path("artifacts/phase2f7h3/svg")
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture = _SessionFixture()
    for tag, (composition, best_marker_path) in WINNING_MARKERS.items():
        if not best_marker_path.exists():
            print(f"[render] skip {tag}: no best-marker.json at {best_marker_path}")
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
