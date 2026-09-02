import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { api } from "../api";
import type { MarkerPreview } from "../types";
import { NestingLabPage } from "./NestingLabPage";

vi.mock("../api", () => ({ api: { listPatternSets: vi.fn(), listFabrics: vi.fn(), listTables: vi.fn(), previewMarker: vi.fn() } }));
const marker: MarkerPreview = {
  status: "VALIDATED_FEASIBLE", search_status: "FEASIBLE_NOT_PROVEN_BEST", warning: "Laboratorio geométrico — no es todavía un plan de producción.",
  marker_length_cm: 30, usable_width_cm: 176, physical_width_cm: 180, piece_count: 1, piece_area_total_cm2: 100,
  marker_area_cm2: 5280, waste_area_cm2: 5180, efficiency_percentage: 1.89, waste_percentage: 98.11,
  lower_bound_length_cm: 4, area_lower_bound_cm: 3, gap_to_area_lower_bound_cm: 27,
  validation: { status: "VALIDATED", checks: {}, errors: [], pair_checks: 0 }, algorithm: "DETERMINISTIC_IRREGULAR_BOTTOM_LEFT_FILL",
  algorithm_version: "v1", nfp_status: "PARTIAL", seed: 1, piece_order_strategy: "AREA_DESC", candidate_order: "MIN_X_THEN_MIN_Y",
  transform_order: [0, 180], evaluation_count: 12, stopping_reason: "done", elapsed_time_ms: 8,
  input_hash: "input", result_hash: "result-hash", cache: { hits: 1, misses: 1 }, diagnostics: [],
  margins_cm: { left: 2, right: 2, start: 2, end: 2 }, clearance_cm: .5, max_length_cm: 700, debug_geometry: null,
  placements: [{ piece_instance_id: "M_FRONT_001", pattern_piece_id: "p", size_code: "M", piece_code: "FRONT",
    transform: { rotation: 0, mirrored: false }, translation: { x_units: 2000, y_units: 2000, x_cm: 2, y_cm: 2 },
    transformed_polygon: { unit: "geometry_unit", coordinates: [[[2000, 2000], [12000, 2000], [12000, 22000], [2000, 22000], [2000, 2000]]] },
    grainline: { unit: "geometry_unit", start: [7000, 4000], end: [7000, 20000] }, bbox: { units: [2000, 2000, 12000, 22000], cm: [2, 2, 12, 22] }, geometry_hash: "g", sequence: 1 }],
};

beforeEach(() => {
  vi.mocked(api.listPatternSets).mockResolvedValue([{ id: "set", version_code: "PATTERN-v1" } as never]);
  vi.mocked(api.listFabrics).mockResolvedValue([{ id: "fabric", display_name: "Tela 180" } as never]);
  vi.mocked(api.listTables).mockResolvedValue([{ id: "table" } as never]);
  vi.mocked(api.previewMarker).mockResolvedValue(marker);
});

test("generates a backend marker and exposes the technical certificate", async () => {
  const user = userEvent.setup(); const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><NestingLabPage /></QueryClientProvider>);
  expect(screen.getByText("Laboratorio geométrico — no es todavía un plan de producción.")).toBeInTheDocument();
  await user.click(await screen.findByRole("button", { name: "GENERAR MARCADOR" }));
  expect(await screen.findByRole("img", { name: /Marcador real validado/ })).toBeInTheDocument();
  expect(screen.getByText("VALIDATED_FEASIBLE")).toBeInTheDocument();
  expect(api.previewMarker).toHaveBeenCalledWith(expect.objectContaining({ composition: [{ size_code: "M", quantity: 1 }] }));
});
