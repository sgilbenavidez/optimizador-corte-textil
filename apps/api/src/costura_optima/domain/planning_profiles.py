PROFILES = {
    "MAX_ORDER_PER_CUT": (
        "spreads", "primary_coverage", "marker_designs", "marker_changeovers",
        "fabric", "total_overproduction", "waste",
    ),
    "MIN_FABRIC": ("total_overproduction", "fabric", "waste", "spreads"),
    "MIN_SPREADS": ("total_overproduction", "spreads", "fabric", "waste"),
    "BALANCED": ("max_overproduction", "total_overproduction", "fabric", "spreads", "waste"),
    "CONSOLIDATED_PRODUCTION": ("total_overproduction", "marker_designs", "spreads", "fabric", "waste"),
    "CONSOLIDATED_MARKERS": ("total_overproduction", "marker_designs", "spreads", "fabric", "waste"),
    "MIN_MARKER_CHANGES": ("total_overproduction", "marker_designs", "spreads", "fabric", "waste"),
}

MAXIMIZE_OBJECTIVES = {"primary_coverage"}
