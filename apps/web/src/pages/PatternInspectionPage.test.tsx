import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { api } from "../api";
import type { PatternPieceGeometry, PatternPieceSummary, PatternSet } from "../types";
import { PatternInspectionPage } from "./PatternInspectionPage";

vi.mock("../api", () => ({ api: { listPatternSets: vi.fn(), getPatternSet: vi.fn(), getPatternPieceGeometry: vi.fn() } }));

const box = { valid: true, validity_reason: "Valid Geometry", area_cm2: 100, perimeter_cm: 40, bbox_cm: { min_x: 0, min_y: 0, max_x: 10, max_y: 20 } };
const codes = ["FRONT", "BACK", "SLEEVE", "NECKBAND"] as const;
const summaries: PatternPieceSummary[] = codes.map((piece_code) => ({
  id: piece_code, size_code: "M", piece_code, quantity: piece_code === "SLEEVE" ? 2 : 1,
  grainline: { kind: "STRAIGHT_GRAIN", unit: "cm", start: [5, 3], end: [5, 17] },
  allowed_rotations_degrees: [0, 180], mirror_allowed: false, edge_allowances_cm: { GENERAL: 1 },
  technical_measurements: {}, geometry_metrics: { seamline: box, cutline: box }, geometry_hash: `hash-${piece_code}`,
}));
const patternSet: PatternSet = {
  id: "set-1", garment_model_version_id: "model-v0", parameter_profile_id: "profile-1", version: 1,
  version_code: "PATTERN-v1", algorithm_version: "bezier-v1", measurement_snapshot_hash: "m", content_hash: "h",
  lifecycle_status: "ENGINEERING", validation_status: "UNVALIDATED_FOR_PRODUCTION", unit: "cm", geometry_units_per_cm: 1000,
  warning: "Patrón experimental de ingeniería — no validado para producción.", created_at: "2026-09-01T00:00:00Z", pieces: summaries,
};
const geometries: PatternPieceGeometry[] = summaries.map((piece) => ({
  ...piece, pattern_set_version_id: "set-1",
  source_geometry: { type: "STRUCTURED_PATH", closed: true, segments: [
    { kind: "LINE", start: [0, 0], end: [10, 0], edge: "HEM" },
    { kind: "LINE", start: [10, 0], end: [10, 20], edge: "SIDE" },
    { kind: "LINE", start: [10, 20], end: [0, 20], edge: "TOP" },
    { kind: "LINE", start: [0, 20], end: [0, 0], edge: "SIDE" },
  ] },
  seamline_geometry: { coordinates: [[[0, 0], [10, 0], [10, 20], [0, 20], [0, 0]]] },
  cutline_geometry: { coordinates: [[[0, 0], [10, 0], [10, 20], [0, 20], [0, 0]]] },
  operational_geometry: { units_per_cm: 1000, coordinates: [[[0, 0], [10000, 0], [10000, 20000], [0, 20000], [0, 0]]] },
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><PatternInspectionPage /></QueryClientProvider>);
}

beforeEach(() => {
  vi.mocked(api.listPatternSets).mockResolvedValue([patternSet]);
  vi.mocked(api.getPatternSet).mockResolvedValue(patternSet);
  vi.mocked(api.getPatternPieceGeometry).mockImplementation(async (id) => geometries.find((piece) => piece.id === id)!);
});

test("loads the four separated pieces and engineering warning", async () => {
  renderPage();
  expect(await screen.findByText("FRONT × 1")).toBeInTheDocument();
  expect(screen.getByText("SLEEVE × 2")).toBeInTheDocument();
  expect(screen.getByText(/Patrón experimental de ingeniería/)).toBeInTheDocument();
  expect(screen.getByRole("img")).toHaveAccessibleName(/no es un nesting/i);
});

test("changes size and toggles a technical layer", async () => {
  const user = userEvent.setup(); renderPage();
  await screen.findByText("FRONT × 1");
  await user.selectOptions(screen.getByLabelText("Talla"), "XL");
  await waitFor(() => expect(api.getPatternSet).toHaveBeenCalledWith("set-1", "XL"));
  const cutLayer = screen.getByLabelText("Corte");
  await user.click(cutLayer);
  expect(cutLayer).not.toBeChecked();
});
