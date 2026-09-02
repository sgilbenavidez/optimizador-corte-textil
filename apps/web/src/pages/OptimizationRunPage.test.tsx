import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, test, vi } from "vitest";
import { api } from "../api";
import { OptimizationRunPage } from "./OptimizationRunPage";

vi.mock("../api", () => ({ api: {
  getOptimizationRun: vi.fn(), listOptimizationSolutions: vi.fn(), getOptimizationSolution: vi.fn(),
  getOptimizationAudit: vi.fn(), getMarkerArtifact: vi.fn(), cancelOptimizationRun: vi.fn(),
} }));

const summary = {
  id: "solution", solution_hash: "solution-hash", profiles: ["MIN_FABRIC", "MIN_SPREADS"], rank: 1,
  planning_status: "OPTIMAL", planning_optimality: "SOLVER_OPTIMAL", solution_origin: "CP_SAT_VALIDATED_CATALOG",
  metrics: { total_linear_consumption_m: 2.1, spread_count: 1, total_overproduction: 0 },
  validation: { status: "VALIDATED_PLAN" }, explanation: "Consume 2.10 m lineales sin excedentes.",
};

beforeEach(() => {
  vi.mocked(api.getOptimizationRun).mockResolvedValue({ id: "run", status: "SUCCEEDED" } as never);
  vi.mocked(api.listOptimizationSolutions).mockResolvedValue([summary] as never);
  vi.mocked(api.getOptimizationSolution).mockResolvedValue({
    ...summary, run_id: "run",
    metrics: { ...summary.metrics, produced_by_size: { S: 3 }, global_efficiency_percentage: 68.44 },
    size_results: [{ size_code: "S", requested: 3, produced: 3, overproduction: 0 }],
    spreads: [{ id: "spread", solution_id: "solution", marker_hash: "marker", spread_hash: "spread-hash", sequence: 1,
      layers: 3, repeats: 1, composition: { S: 1 }, production_by_size: { S: 3 }, marker_length_cm: 69.8,
      fabric_consumption_m: 2.094, marker_efficiency_percentage: 68.44,
      marker_search_status: "FEASIBLE_NOT_PROVEN_BEST", validation_status: "VALIDATED" }],
  } as never);
  vi.mocked(api.getOptimizationAudit).mockResolvedValue({ run_id: "run" });
});

test("renders a validated solution with size and executable spread data", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/optimization-runs/run"]}>
    <Routes><Route path="/optimization-runs/:runId" element={<OptimizationRunPage />} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  expect(await screen.findByRole("heading", { name: "Plan de corte validado" })).toBeInTheDocument();
  expect(await screen.findAllByRole("cell", { name: "3" })).toHaveLength(2);
  const spread = await screen.findByRole("button", { name: /TENDIDO 1/ });
  expect(spread).toHaveAccessibleName(/3 capas/);
});
