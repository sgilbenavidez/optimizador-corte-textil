import hashlib
import json
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from costura_optima.domain.enums import LifecycleStatus, ProvenanceStatus, ValidationStatus
from costura_optima.infrastructure.db_models import (
    CuttingTableConfigurationORM,
    FabricConfigurationORM,
    GarmentModelORM,
    GarmentModelVersionORM,
    GarmentTypeORM,
    MeasurementValueORM,
    PatternParameterProfileORM,
    SizeDefinitionORM,
)
from costura_optima.patterns.profile import ENGINEERING_PROFILE, PROFILE_CODE


SOURCE_REFERENCE = "PHASE_0_APPROVED_WITH_CONDITIONS_2026-09-01"
SIZE_CODES = ["XS", "S", "M", "L", "XL", "XXL", "XXXL"]

BODY_TABLE = {
    "XS": [84, 72, 88, 35.0, 27, 39.0, 176],
    "S": [92, 80, 96, 36.5, 29, 40.5, 176],
    "M": [100, 88, 104, 38.0, 31, 42.0, 176],
    "L": [108, 96, 112, 39.5, 33, 43.5, 176],
    "XL": [116, 104, 120, 41.0, 35, 45.0, 176],
    "XXL": [124, 112, 128, 42.5, 37, 46.5, 176],
    "XXXL": [132, 120, 136, 44.0, 39, 48.0, 176],
}
GARMENT_TABLE = {
    "XS": [92, 92, 64, 40.0, 19, 32],
    "S": [100, 100, 66, 41.5, 20, 34],
    "M": [108, 108, 68, 43.0, 21, 36],
    "L": [116, 116, 70, 44.5, 22, 38],
    "XL": [124, 124, 72, 46.0, 23, 40],
    "XXL": [132, 132, 74, 47.5, 24, 42],
    "XXXL": [140, 140, 76, 49.0, 25, 44],
}
BODY_CODES = ["CHEST_CIRCUMFERENCE", "WAIST_CIRCUMFERENCE", "HIP_CIRCUMFERENCE", "NECK_BASE", "BICEPS", "SHOULDER_WIDTH", "STATURE"]
GARMENT_CODES = ["FINISHED_CHEST", "FINISHED_HEM", "HPS_LENGTH", "FINISHED_SHOULDERS", "SHORT_SLEEVE_LENGTH", "SLEEVE_OPENING"]

DERIVATIONS = {
    "CHEST_CIRCUMFERENCE": "84 + 8 * size_index",
    "WAIST_CIRCUMFERENCE": "body_chest - 12",
    "HIP_CIRCUMFERENCE": "body_chest + 4",
    "NECK_BASE": "35 + 1.5 * size_index",
    "BICEPS": "27 + 2 * size_index",
    "SHOULDER_WIDTH": "39 + 1.5 * size_index",
    "STATURE": "176 (nominal engineering assumption)",
    "FINISHED_CHEST": "body_chest + 8",
    "FINISHED_HEM": "max(finished_chest, body_hip + 4)",
    "HPS_LENGTH": "64 + 2 * size_index",
    "FINISHED_SHOULDERS": "body_shoulders + 1",
    "SHORT_SLEEVE_LENGTH": "19 + size_index",
    "SLEEVE_OPENING": "body_biceps + 5",
}


def stable_id(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"https://costura-optima.local/seed/{key}"))


def canonical_hash(value: dict) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def seed_definition() -> dict:
    return {
        "market_profile": "ADULT_UNISEX_STRAIGHT_COMMERCIAL",
        "version_code": "TSHIRT-REGULAR-STRAIGHT-ADULT-v0",
        "lifecycle_status": "ENGINEERING",
        "validation_status": "UNVALIDATED_FOR_PRODUCTION",
        "sizes": [
            {"code": code, "body": BODY_TABLE[code], "garment": GARMENT_TABLE[code]}
            for code in SIZE_CODES
        ],
    }


def seed_catalog(session: Session) -> dict[str, int]:
    garment_type = session.scalar(select(GarmentTypeORM).where(GarmentTypeORM.code == "T_SHIRT"))
    if garment_type is None:
        garment_type = GarmentTypeORM(
            id=stable_id("garment-type/T_SHIRT"), code="T_SHIRT", display_name="Camiseta"
        )
        session.add(garment_type)
        session.flush()

    model = session.scalar(select(GarmentModelORM).where(GarmentModelORM.code == "TSHIRT_REGULAR_STRAIGHT_ADULT"))
    if model is None:
        model = GarmentModelORM(
            id=stable_id("garment-model/TSHIRT_REGULAR_STRAIGHT_ADULT"),
            garment_type_id=garment_type.id,
            code="TSHIRT_REGULAR_STRAIGHT_ADULT",
            display_name="Camiseta básica regular",
            origin_type="STANDARD",
        )
        session.add(model)
        session.flush()

    version = session.scalar(
        select(GarmentModelVersionORM).where(
            GarmentModelVersionORM.version_code == "TSHIRT-REGULAR-STRAIGHT-ADULT-v0"
        )
    )
    if version is None:
        version = GarmentModelVersionORM(
            id=stable_id("garment-version/TSHIRT-REGULAR-STRAIGHT-ADULT-v0"),
            garment_model_id=model.id,
            version_code="TSHIRT-REGULAR-STRAIGHT-ADULT-v0",
            display_name="Camiseta básica regular",
            market_profile="ADULT_UNISEX_STRAIGHT_COMMERCIAL",
            lifecycle_status=LifecycleStatus.ENGINEERING,
            validation_status=ValidationStatus.UNVALIDATED_FOR_PRODUCTION,
            is_orderable=True,
            unit="cm",
            content_hash=canonical_hash(seed_definition()),
            provenance={
                "status": "ASSUMPTION",
                "source_reference": SOURCE_REFERENCE,
                "accessed_at": "2026-09-01",
                "notice": "Sistema interno de ingeniería; no es ISO, ASTM ni validado para Colombia.",
            },
        )
        session.add(version)
        session.flush()

    for index, code in enumerate(SIZE_CODES):
        size = session.scalar(
            select(SizeDefinitionORM).where(
                SizeDefinitionORM.garment_model_version_id == version.id,
                SizeDefinitionORM.code == code,
            )
        )
        if size is None:
            size = SizeDefinitionORM(
                id=stable_id(f"size/{version.version_code}/{code}"),
                garment_model_version_id=version.id,
                code=code,
                display_order=index,
                label=code,
            )
            session.add(size)
            session.flush()

        values = [(measurement_code, "BODY", value) for measurement_code, value in zip(BODY_CODES, BODY_TABLE[code])]
        values += [(measurement_code, "GARMENT", value) for measurement_code, value in zip(GARMENT_CODES, GARMENT_TABLE[code])]
        for measurement_code, measurement_kind, value in values:
            exists = session.scalar(
                select(MeasurementValueORM.id).where(
                    MeasurementValueORM.size_definition_id == size.id,
                    MeasurementValueORM.measurement_code == measurement_code,
                )
            )
            if exists is None:
                session.add(
                    MeasurementValueORM(
                        id=stable_id(f"measurement/{version.version_code}/{code}/{measurement_code}"),
                        size_definition_id=size.id,
                        measurement_code=measurement_code,
                        measurement_kind=measurement_kind,
                        value=Decimal(str(value)),
                        unit="cm",
                        provenance_status=ProvenanceStatus.ASSUMPTION,
                        source_reference=SOURCE_REFERENCE,
                        derivation=DERIVATIONS[measurement_code],
                        version="v0",
                    )
                )

    fabric_payload = {
        "version_code": "KNIT-JERSEY-180-176-v1",
        "physical_width_cm": 180,
        "usable_width_cm": 176,
        "fabric_family": "KNIT_JERSEY",
        "directional": False,
        "lay_mode": "OPEN_WIDTH_FACE_ONE_WAY",
        "lay_face_mode": "FACE_ONE_WAY",
        "marker_direction_policy": "TWO_WAY",
        "fabric_directionality": "NON_DIRECTIONAL",
        "piece_clearance_cm": 0.5,
        "margins_cm": {"left": 2, "right": 2, "start": 2, "end": 2},
    }
    fabric = session.scalar(
        select(FabricConfigurationORM).where(FabricConfigurationORM.version_code == fabric_payload["version_code"])
    )
    if fabric is None:
        fabric = FabricConfigurationORM(
            id=stable_id("fabric/KNIT-JERSEY-180-176-v1"),
            version_code=fabric_payload["version_code"],
            display_name="Jersey 1,80 m — ancho útil 1,76 m",
            physical_width_cm=Decimal("180"),
            usable_width_cm=Decimal("176"),
            fabric_family="KNIT_JERSEY",
            directional=False,
            lay_mode="OPEN_WIDTH_FACE_ONE_WAY",
            lay_face_mode="FACE_ONE_WAY",
            marker_direction_policy="TWO_WAY",
            fabric_directionality="NON_DIRECTIONAL",
            piece_clearance_cm=Decimal("0.5"),
            left_margin_cm=Decimal("2"),
            right_margin_cm=Decimal("2"),
            start_margin_cm=Decimal("2"),
            end_margin_cm=Decimal("2"),
            content_hash=canonical_hash(fabric_payload),
            is_active=True,
        )
        session.add(fabric)

    table_payload = {
        "version_code": "CUTTING-TABLE-800-700-30-v1",
        "physical_length_cm": 800,
        "usable_length_cm": 700,
        "max_layers": 30,
    }
    table = session.scalar(
        select(CuttingTableConfigurationORM).where(
            CuttingTableConfigurationORM.version_code == table_payload["version_code"]
        )
    )
    if table is None:
        table = CuttingTableConfigurationORM(
            id=stable_id("table/CUTTING-TABLE-800-700-30-v1"),
            version_code=table_payload["version_code"],
            display_name="Mesa 8 m — largo útil 7 m",
            physical_length_cm=Decimal("800"),
            usable_length_cm=Decimal("700"),
            max_layers=30,
            content_hash=canonical_hash(table_payload),
            is_active=True,
        )
        session.add(table)

    profile_hash = canonical_hash(ENGINEERING_PROFILE)
    parameter_profile = session.scalar(
        select(PatternParameterProfileORM).where(PatternParameterProfileORM.content_hash == profile_hash)
    )
    if parameter_profile is None:
        parameter_profile = PatternParameterProfileORM(
            id=stable_id(f"pattern-parameter-profile/{profile_hash}"),
            code=PROFILE_CODE,
            version=1,
            parameters=ENGINEERING_PROFILE,
            content_hash=profile_hash,
            lifecycle_status=LifecycleStatus.ENGINEERING,
            validation_status=ValidationStatus.UNVALIDATED_FOR_PRODUCTION,
        )
        session.add(parameter_profile)

    session.commit()
    return {
        "garment_types": session.scalar(select(func.count()).select_from(GarmentTypeORM)) or 0,
        "garment_models": session.scalar(select(func.count()).select_from(GarmentModelORM)) or 0,
        "garment_versions": session.scalar(select(func.count()).select_from(GarmentModelVersionORM)) or 0,
        "sizes": session.scalar(select(func.count()).select_from(SizeDefinitionORM)) or 0,
        "measurements": session.scalar(select(func.count()).select_from(MeasurementValueORM)) or 0,
        "fabrics": session.scalar(select(func.count()).select_from(FabricConfigurationORM)) or 0,
        "tables": session.scalar(select(func.count()).select_from(CuttingTableConfigurationORM)) or 0,
        "pattern_parameter_profiles": session.scalar(select(func.count()).select_from(PatternParameterProfileORM)) or 0,
    }
