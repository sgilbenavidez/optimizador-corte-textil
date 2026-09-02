from costura_optima.infrastructure.database import SessionLocal
from costura_optima.infrastructure.seed_data import seed_catalog


def main() -> None:
    with SessionLocal() as session:
        counts = seed_catalog(session)
    print(f"Seed completado de forma idempotente: {counts}")


if __name__ == "__main__":
    main()
