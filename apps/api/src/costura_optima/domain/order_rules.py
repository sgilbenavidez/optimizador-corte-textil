from collections.abc import Iterable
from dataclasses import dataclass

from costura_optima.domain.errors import ValidationError


@dataclass(frozen=True)
class DemandLine:
    size_code: str
    quantity: int


def normalize_demand(lines: Iterable[DemandLine], allowed_sizes: set[str]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for line in lines:
        code = line.size_code.strip().upper()
        if code in normalized:
            raise ValidationError(f"La talla {code} está duplicada.")
        if code not in allowed_sizes:
            raise ValidationError(f"La talla {code} no existe en la versión seleccionada.")
        if isinstance(line.quantity, bool) or not isinstance(line.quantity, int):
            raise ValidationError("Las cantidades deben ser números enteros.")
        if line.quantity < 0:
            raise ValidationError("Las cantidades no pueden ser negativas.")
        normalized[code] = line.quantity

    positive = {code: quantity for code, quantity in normalized.items() if quantity > 0}
    if not positive:
        raise ValidationError("Debe existir al menos una talla con cantidad mayor que cero.")
    return positive


def max_overproduction_for(demand: int) -> int:
    """Approved default policy, exposed as snapshot data for future runs."""
    if demand <= 0:
        return 0
    return max(2, (demand * 3 + 99) // 100)

