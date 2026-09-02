from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from costura_optima.infrastructure.db_models import (
    CuttingTableConfigurationORM,
    FabricConfigurationORM,
    GarmentModelORM,
    GarmentModelVersionORM,
    PatternPieceORM,
    PatternSetVersionORM,
    ProductionOrderORM,
    SizeDefinitionORM,
)


class CatalogRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_models(self) -> list[GarmentModelORM]:
        statement = (
            select(GarmentModelORM)
            .options(
                selectinload(GarmentModelORM.versions)
                .selectinload(GarmentModelVersionORM.sizes)
                .selectinload(SizeDefinitionORM.measurements)
            )
            .order_by(GarmentModelORM.display_name)
        )
        return list(self.session.scalars(statement).unique())

    def get_version(self, version_id: str) -> GarmentModelVersionORM | None:
        statement = (
            select(GarmentModelVersionORM)
            .where(GarmentModelVersionORM.id == version_id)
            .options(
                selectinload(GarmentModelVersionORM.model),
                selectinload(GarmentModelVersionORM.sizes).selectinload(SizeDefinitionORM.measurements),
            )
        )
        return self.session.scalar(statement)

    def list_fabrics(self) -> list[FabricConfigurationORM]:
        return list(self.session.scalars(select(FabricConfigurationORM).where(FabricConfigurationORM.is_active.is_(True))))

    def get_fabric(self, config_id: str) -> FabricConfigurationORM | None:
        return self.session.get(FabricConfigurationORM, config_id)

    def list_tables(self) -> list[CuttingTableConfigurationORM]:
        return list(
            self.session.scalars(select(CuttingTableConfigurationORM).where(CuttingTableConfigurationORM.is_active.is_(True)))
        )

    def get_table(self, config_id: str) -> CuttingTableConfigurationORM | None:
        return self.session.get(CuttingTableConfigurationORM, config_id)


class OrderRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, order: ProductionOrderORM) -> ProductionOrderORM:
        self.session.add(order)
        self.session.commit()
        self.session.refresh(order)
        return order

    def get(self, order_id: str) -> ProductionOrderORM | None:
        statement = (
            select(ProductionOrderORM)
            .where(ProductionOrderORM.id == order_id)
            .options(selectinload(ProductionOrderORM.demands))
        )
        return self.session.scalar(statement)

    def list(self) -> list[ProductionOrderORM]:
        statement = select(ProductionOrderORM).options(selectinload(ProductionOrderORM.demands)).order_by(
            ProductionOrderORM.created_at.desc()
        )
        return list(self.session.scalars(statement))


class PatternRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_sets(self, garment_model_version_id: str | None = None) -> list[PatternSetVersionORM]:
        statement = select(PatternSetVersionORM).options(selectinload(PatternSetVersionORM.pieces))
        if garment_model_version_id:
            statement = statement.where(PatternSetVersionORM.garment_model_version_id == garment_model_version_id)
        return list(self.session.scalars(statement.order_by(PatternSetVersionORM.version.desc())))

    def latest_for_model_version(self, version_id: str) -> PatternSetVersionORM | None:
        return self.session.scalar(
            select(PatternSetVersionORM)
            .where(PatternSetVersionORM.garment_model_version_id == version_id)
            .options(selectinload(PatternSetVersionORM.pieces))
            .order_by(PatternSetVersionORM.version.desc())
            .limit(1)
        )

    def get_set(self, pattern_set_id: str) -> PatternSetVersionORM | None:
        return self.session.scalar(
            select(PatternSetVersionORM)
            .where(PatternSetVersionORM.id == pattern_set_id)
            .options(selectinload(PatternSetVersionORM.pieces))
        )

    def get_piece(self, piece_id: str) -> PatternPieceORM | None:
        return self.session.get(PatternPieceORM, piece_id)
