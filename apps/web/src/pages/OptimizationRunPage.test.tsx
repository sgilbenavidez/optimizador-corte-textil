import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, test, vi } from "vitest";
import { api } from "../api";
import { OptimizationRunPage } from "./OptimizationRunPage";

vi.mock("../api", () => ({ api: {
  getOptimizationRun: vi.fn(), listOptimizationSolutions: vi.fn(), getOptimizationSolution: vi.fn(),
  getOptimizationAudit: vi.fn(), getMarkerArtifact: vi.fn(), cancelOptimizationRun: vi.fn(),
  useCurrentOptimizationPlan: vi.fn(),
  retryOptimizationRun: vi.fn(), exportOptimizationRun: vi.fn(), markerSvgUrl: vi.fn((hash: string) => `/markers/${hash}/svg`),
} }));

const summary = {
  id: "solution", solution_hash: "solution-hash", profiles: ["MIN_FABRIC", "BALANCED"], rank: 1,
  planning_status: "OPTIMAL", planning_optimality: "SOLVER_OPTIMAL", solution_origin: "CP_SAT_VALIDATED_CATALOG",
  metrics: { total_linear_consumption_m: 2.1, spread_count: 1, total_overproduction: 0 },
  validation: { status: "VALIDATED_PLAN" }, explanation: "Consume 2.10 m lineales sin excedentes.",
};

beforeEach(() => {
  vi.mocked(api.getOptimizationRun).mockResolvedValue({ id: "run", status: "SUCCEEDED" } as never);
  vi.mocked(api.listOptimizationSolutions).mockResolvedValue([summary] as never);
  vi.mocked(api.getOptimizationSolution).mockResolvedValue({
    ...summary, run_id: "run",
    metrics: { ...summary.metrics, requested_by_size: { S: 3, M: 20, L: 10, XL: 12, XXL: 30 }, produced_by_size: { S: 3 }, global_efficiency_percentage: 68.44, solver_time_ms: 12 },
    size_results: [{ size_code: "S", requested: 3, produced: 3, overproduction: 0 }],
    spreads: [{ id: "spread", solution_id: "solution", marker_hash: "marker", spread_hash: "spread-hash", sequence: 1,
      layers: 3, repeats: 1, composition: { S: 1 }, production_by_size: { S: 3 }, marker_length_cm: 69.8,
      fabric_consumption_m: 2.094, marker_efficiency_percentage: 68.44,
      marker_search_status: "FEASIBLE_NOT_PROVEN_BEST", validation_status: "VALIDATED" }],
  } as never);
  vi.mocked(api.getOptimizationAudit).mockResolvedValue({ run_id: "run" });
  vi.mocked(api.getMarkerArtifact).mockResolvedValue({
    marker_hash: "marker", composition: { S: 1 }, geometry_engine_version: "geometry-v1",
    marker_search_status: "FEASIBLE", validation_status: "VALIDATED",
    marker: { status: "VALIDATED_FEASIBLE", marker_length_cm: 69.8, max_length_cm: 700,
      physical_width_cm: 180, usable_width_cm: 176, margins_cm: { start: 1, end: 1, left: 2, right: 2 },
      placements: [{ piece_instance_id: "front-s-1", pattern_piece_id: "front-s", size_code: "S", piece_code: "FRONT",
        transform: { rotation: 180, mirrored: false }, translation: { x_units: 0, y_units: 0, x_cm: 0, y_cm: 0 },
        transformed_polygon: { unit: "geometry", coordinates: [[[0, 0], [10000, 0], [10000, 10000], [0, 0]]] },
        grainline: { unit: "geometry", start: [1000, 5000], end: [9000, 5000] }, bbox: { units: [0, 0, 10000, 10000], cm: [0, 0, 10, 10] }, geometry_hash: "piece", sequence: 1 }],
    },
  } as never);
});

test("renders a validated solution with size and executable spread data", async () => {
  const user = userEvent.setup();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/optimization-runs/run"]}>
    <Routes><Route path="/optimization-runs/:runId" element={<OptimizationRunPage />} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  expect(await screen.findByRole("heading", { name: "Plan de corte validado" })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "Menor tela · Balanceada" })).toBeInTheDocument();
  expect(await screen.findAllByRole("cell", { name: "3" })).toHaveLength(2);
  expect(await screen.findByRole("button", { name: /PLAN COMPLETO/ })).toHaveAccessibleName(/1 diseños · 1 tendidos físicos/);
  const spread = await screen.findByRole("button", { name: /CORTE 1/ });
  expect(spread).toHaveAccessibleName(/3 capas/);
  expect(screen.getByText("PEDIDO").parentElement).toHaveTextContent("75 prendas");
  for (const heading of ["Perfil", "Tela", "Diseños", "Cambios", "Tendidos físicos", "Producción total", "Sobreproducción", "Eficiencia global", "Planning status", "Tiempo"]) {
    expect(screen.getByRole("columnheader", { name: heading })).toBeInTheDocument();
  }
  expect(screen.getByText("Objective stages")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Mapa de corte" })).toBeInTheDocument();
  expect(await screen.findByRole("img", { name: "Marcador real validado geométricamente" })).toHaveTextContent("FRONT · S");
  await user.click(spread);
  expect(await screen.findByText("PIEZAS 1 / 1")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Descargar mapa SVG" })).toHaveAttribute("href", "/markers/marker/svg");
});

test("a FEASIBLE recommendation never claims optimality", async () => {
  vi.mocked(api.listOptimizationSolutions).mockResolvedValue([{ ...summary, planning_status: "FEASIBLE", planning_optimality: "SOLVER_FEASIBLE" }] as never);
  vi.mocked(api.getOptimizationSolution).mockResolvedValue({
    ...await vi.mocked(api.getOptimizationSolution)("solution"), planning_status: "FEASIBLE", planning_optimality: "SOLVER_FEASIBLE",
  } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/optimization-runs/run"]}><Routes><Route path="/optimization-runs/:runId" element={<OptimizationRunPage />} /></Routes></MemoryRouter></QueryClientProvider>);
  const hero = await screen.findByRole("heading", { name: "Plan de corte validado" });
  await screen.findByText(/Mejor planificación validada encontrada/);
  expect(hero.parentElement?.textContent?.toLowerCase()).not.toMatch(/planificación óptima|resultado óptimo|optimal result/);
});

test("shows MENOS CORTES coverage and residual by physical cut", async () => {
  const maxSummary = { ...summary, profiles: ["MAX_ORDER_PER_CUT"], recommended: true,
    metrics: { ...summary.metrics, physical_spreads: 4, marker_design_count: 4, marker_change_count: 3,
      primary_spread_coverage_percentage: 82.474227 } };
  vi.mocked(api.listOptimizationSolutions).mockResolvedValue([maxSummary] as never);
  const detail = await vi.mocked(api.getOptimizationSolution)("solution") as never as Record<string, unknown>;
  vi.mocked(api.getOptimizationSolution).mockResolvedValue({ ...detail, ...maxSummary,
    metrics: { ...((detail.metrics ?? {}) as Record<string, unknown>), ...maxSummary.metrics },
    spreads: [{ ...(detail.spreads as Array<Record<string, unknown>>)[0], useful_garments: 80,
      order_coverage_percentage: 82.474227, remaining_demand_after: { M: 10, XL: 4, XXL: 3 }, is_primary: true }],
  } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/optimization-runs/run"]}>
    <Routes><Route path="/optimization-runs/:runId" element={<OptimizationRunPage />} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  expect(await screen.findByRole("button", { name: "MENOS CORTES" })).toBeInTheDocument();
  expect(screen.getByText("Produce la mayor cantidad posible del pedido en cada tendido.")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Cobertura primer corte" })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /CORTE 1 · PRINCIPAL.*80 prendas útiles.*82.5%/ })).toBeInTheDocument();
  expect(screen.getByText(/M 10 · XL 4 · XXL 3/)).toBeInTheDocument();
});

test("shows an anytime incumbent and lets the operator use it", async () => {
  const user = userEvent.setup();
  vi.mocked(api.getOptimizationRun).mockResolvedValue({
    id: "run", production_order_id: "order", status: "RUNNING", phase: "PLANNING_REFINEMENT",
    progress: { candidates_evaluated: 8, candidates_feasible: 7, best_feasible_found: true },
    best_solution_available: true, incumbent: { first_solution_spreads: 3, first_solution_fabric_m: 48.25 },
    elapsed_ms: 5000, updated_at: new Date().toISOString(), round_current: 1, round_total_if_known: 2,
    use_current_plan_requested: false, cancel_requested: false,
  } as never);
  vi.mocked(api.useCurrentOptimizationPlan).mockResolvedValue({} as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/optimization-runs/run"]}>
    <Routes><Route path="/optimization-runs/:runId" element={<OptimizationRunPage />} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("Ya encontramos un plan válido. Estamos buscando una alternativa mejor.")).toBeInTheDocument();
  expect(screen.getByText(/Plan válido: 3 cortes · 48.25 m/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "USAR MEJOR PLAN ACTUAL" }));
  expect(api.useCurrentOptimizationPlan).toHaveBeenCalledWith("run");
});
