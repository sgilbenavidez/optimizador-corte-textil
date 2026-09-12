from costura_optima.domain.divisibility import DivisibilityRatioGenerator


def test_benchmark_216_exact_subset_ratios_are_enumerated():
    demand = {"XS": 30, "S": 33, "M": 42, "L": 21, "XL": 35, "XXL": 55}
    rows = DivisibilityRatioGenerator().generate(demand, max_layers=30)
    found = {(row.composition, row.layers, row.gcd_value) for row in rows}
    assert ((("L", 1), ("M", 2)), 21, 21) in found
    assert ((("S", 3), ("XXL", 5)), 11, 11) in found
    assert ((("XL", 7), ("XS", 6)), 5, 5) in found
    assert all(all(value == 0 for value in row.residual.values()) for row in rows)
