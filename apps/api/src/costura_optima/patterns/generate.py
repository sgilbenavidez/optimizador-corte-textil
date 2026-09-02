from argparse import ArgumentParser
from pathlib import Path

from costura_optima.infrastructure.database import SessionLocal
from costura_optima.patterns.persistence import generate_and_persist


def main() -> None:
    parser = ArgumentParser(description="Generate immutable engineering pattern geometry.")
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args()
    with SessionLocal() as session:
        pattern_set, created = generate_and_persist(session, args.artifacts_dir)
    action = "created" if created else "reused"
    print(f"Engineering pattern set {action}: {pattern_set.version_code} ({pattern_set.content_hash})")


if __name__ == "__main__":
    main()
