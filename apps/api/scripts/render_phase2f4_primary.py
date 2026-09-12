"""Render the certified Phase 2F.4 primary marker as an annotated golden PNG."""
from pathlib import Path

from resvg_py import svg_to_bytes


source = Path("artifacts/phase2f4/primary-spread.svg")
target_svg = Path("artifacts/phase2f4/primary-spread-golden.svg")
target_png = Path("artifacts/phase2f4/primary-spread-golden.png")
svg = source.read_text(encoding="utf-8")
svg = svg.replace('viewBox="0 0 603409 180000"', 'viewBox="0 0 603409 250000"', 1)
opening_end = svg.index(">") + 1
banner = (
    '<rect width="603409" height="70000" fill="#163b40"/>'
    '<text x="18000" y="23500" fill="#ffffff" font-size="15000" font-family="Arial" font-weight="700">'
    'PRIMARY_SPREAD · CORTE 1 · 82.47% DEL PEDIDO</text>'
    '<text x="18000" y="47000" fill="#d8ebe6" font-size="11500" font-family="Arial">'
    'XS×2 · S×1 · M×2 · L×2 · XL×1  |  10 capas  |  80 prendas  |  40 piezas</text>'
    '<g transform="translate(0 70000)">'
)
annotated = svg[:opening_end] + banner + svg[opening_end:].replace("</svg>", "</g></svg>", 1)
target_svg.write_text(annotated, encoding="utf-8")
target_png.write_bytes(svg_to_bytes(svg_string=annotated, width=1600, height=663, background="#fffdf7"))
print(target_png)
