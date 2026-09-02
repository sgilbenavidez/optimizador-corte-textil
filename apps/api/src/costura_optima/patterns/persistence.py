from __future__ import annotations

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from costura_optima.infrastructure.db_models import (
    GarmentModelVersionORM,
    PatternParameterProfileORM,
    PatternPieceORM,
    PatternSetVersionORM,
    SizeDefinitionORM,
)
from costura_optima.patterns.generator import EngineeringPatternGenerator, GeneratedPiece, canonical_hash, validate_compatibility
from costura_optima.patterns.profile import PROFILE_CODE


PATTERN_WARNING = "Patrón experimental de ingeniería — no validado para producción."


def _stable_id(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"https://costura-optima.local/pattern/{key}"))


def _measurement_snapshot(version: GarmentModelVersionORM) -> dict:
    return {
        size.code: {
            measurement.measurement_code: float(measurement.value)
            for measurement in sorted(size.measurements, key=lambda item: item.measurement_code)
        }
        for size in sorted(version.sizes, key=lambda item: item.display_order)
    }


def generate_and_persist(session: Session, artifacts_dir: Path | None = None) -> tuple[PatternSetVersionORM, bool]:
    version = session.scalar(
        select(GarmentModelVersionORM)
        .where(GarmentModelVersionORM.version_code == "TSHIRT-REGULAR-STRAIGHT-ADULT-v0")
        .options(selectinload(GarmentModelVersionORM.sizes).selectinload(SizeDefinitionORM.measurements))
    )
    profile = session.scalar(select(PatternParameterProfileORM).where(PatternParameterProfileORM.code == PROFILE_CODE))
    if version is None or profile is None:
        raise RuntimeError("Seed catalog and parameter profile before generating patterns.")

    measurement_snapshot = _measurement_snapshot(version)
    measurement_hash = canonical_hash(measurement_snapshot)
    generator = EngineeringPatternGenerator(profile.parameters)
    generated: list[GeneratedPiece] = []
    for size in sorted(version.sizes, key=lambda item: item.display_order):
        pieces = generator.generate_size(size.code, size.display_order, measurement_snapshot[size.code])
        validate_compatibility(pieces, profile.parameters["compatibility_tolerance_cm"])
        generated.extend(pieces)

    content_payload = {
        "garment_model_version_hash": version.content_hash,
        "measurement_snapshot_hash": measurement_hash,
        "parameter_profile_hash": profile.content_hash,
        "algorithm_version": generator.algorithm_version,
        "piece_hashes": [piece.geometry_hash for piece in generated],
    }
    content_hash = canonical_hash(content_payload)
    existing = session.scalar(
        select(PatternSetVersionORM)
        .where(PatternSetVersionORM.content_hash == content_hash)
        .options(selectinload(PatternSetVersionORM.pieces))
    )
    if existing is not None:
        if artifacts_dir:
            export_artifacts(artifacts_dir, existing.pieces)
        return existing, False

    next_version = (session.scalar(select(func.max(PatternSetVersionORM.version)).where(
        PatternSetVersionORM.garment_model_version_id == version.id
    )) or 0) + 1
    pattern_set = PatternSetVersionORM(
        id=_stable_id(f"set/{content_hash}"),
        garment_model_version_id=version.id,
        parameter_profile_id=profile.id,
        version=next_version,
        version_code=f"{version.version_code}-PATTERN-v{next_version}",
        algorithm_version=generator.algorithm_version,
        measurement_snapshot_hash=measurement_hash,
        content_hash=content_hash,
        lifecycle_status="ENGINEERING",
        validation_status="UNVALIDATED_FOR_PRODUCTION",
        unit="cm",
        geometry_units_per_cm=profile.parameters["geometry_units_per_cm"],
        warning=PATTERN_WARNING,
    )
    for item in generated:
        pattern_set.pieces.append(
            PatternPieceORM(
                id=_stable_id(f"piece/{content_hash}/{item.size_code}/{item.piece_code}"),
                size_code=item.size_code,
                piece_code=item.piece_code,
                quantity=item.quantity,
                source_geometry=item.source_geometry,
                seamline_geometry=item.seamline_geometry,
                cutline_geometry=item.cutline_geometry,
                operational_geometry=item.operational_geometry,
                grainline=item.grainline,
                allowed_rotations_degrees=item.allowed_rotations_degrees,
                mirror_allowed=item.mirror_allowed,
                edge_allowances_cm=item.edge_allowances_cm,
                technical_measurements=item.measurements,
                geometry_metrics=item.metrics,
                geometry_hash=item.geometry_hash,
            )
        )
    session.add(pattern_set)
    session.commit()
    session.refresh(pattern_set)
    if artifacts_dir:
        export_artifacts(artifacts_dir, pattern_set.pieces)
    return pattern_set, True


def _polyline(points: list[list[float]], transform) -> str:
    return " ".join(f"{transform(x, y)[0]:.2f},{transform(x, y)[1]:.2f}" for x, y in points)


def export_artifacts(directory: Path, pieces: list[PatternPieceORM]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for size_code in ["XS", "M", "XL", "XXXL"]:
        selected = {piece.piece_code: piece for piece in pieces if piece.size_code == size_code}
        if len(selected) != 4:
            continue
        scale, gap = 6.0, 14.0
        front_box = selected["FRONT"].geometry_metrics["cutline"]["bbox_cm"]
        back_box = selected["BACK"].geometry_metrics["cutline"]["bbox_cm"]
        body_height = max(front_box["max_y"] - front_box["min_y"], back_box["max_y"] - back_box["min_y"])
        placements = {
            "FRONT": (0.0, 0.0),
            "BACK": (front_box["max_x"] - front_box["min_x"] + gap, 0.0),
            "SLEEVE": (0.0, body_height + gap),
            "NECKBAND": (55.0, body_height + gap),
        }
        all_width = 150.0
        all_height = body_height + 55.0
        svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{all_width*scale:.0f}" height="{all_height*scale:.0f}" viewBox="0 0 {all_width*scale:.0f} {all_height*scale:.0f}">',
            '<rect width="100%" height="100%" fill="#f8f5ec"/>',
            f'<text x="20" y="24" font-family="Segoe UI" font-size="16" fill="#173238">Costura Óptima · patrón técnico {size_code}</text>',
            f'<text x="20" y="43" font-family="Segoe UI" font-size="11" fill="#9f352c">{PATTERN_WARNING}</text>',
        ]
        for code, piece in selected.items():
            box = piece.geometry_metrics["cutline"]["bbox_cm"]
            offset_x, offset_y = placements[code]
            def transform(x, y, ox=offset_x, oy=offset_y, b=box):
                return ((ox + x - b["min_x"] + 2) * scale, (oy + b["max_y"] - y + 8) * scale)
            cut = piece.cutline_geometry["coordinates"][0]
            seam = piece.seamline_geometry["coordinates"][0]
            svg.append(f'<polygon points="{_polyline(cut, transform)}" fill="#dcebe5" stroke="#102b32" stroke-width="1.7"/>')
            svg.append(f'<polyline points="{_polyline(seam, transform)}" fill="none" stroke="#d69335" stroke-width="1.1" stroke-dasharray="5 3"/>')
            grain = piece.grainline
            gx1, gy1 = transform(*grain["start"]); gx2, gy2 = transform(*grain["end"])
            svg.append(f'<line x1="{gx1}" y1="{gy1}" x2="{gx2}" y2="{gy2}" stroke="#176a70" stroke-width="1.5"/>')
            svg.append(f'<text x="{(offset_x+2)*scale:.1f}" y="{(offset_y+5)*scale:.1f}" font-family="Segoe UI" font-size="12" font-weight="700" fill="#173238">{code} × {piece.quantity}</text>')
        svg.append("</svg>")
        (directory / f"pattern-{size_code}.svg").write_text("\n".join(svg), encoding="utf-8")
