import type {
  CuttingTableConfiguration,
  FabricConfiguration,
  GarmentModel,
  GarmentSize,
  ProductionOrder,
  PatternPieceGeometry,
  PatternSet,
  MarkerPreview,
  OptimizationRun,
  OptimizationSolution,
  OptimizationSolutionSummary,
  Spread,
  MarkerArtifact,
  Page,
} from "./types";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

type ProblemDetails = { detail?: string; title?: string };

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    const problem = (await response.json().catch(() => ({}))) as ProblemDetails;
    throw new Error(problem.detail ?? problem.title ?? "No fue posible completar la operación.");
  }
  return response.json() as Promise<T>;
}

export const api = {
  listGarmentModels: () => request<GarmentModel[]>("/api/v1/garment-models"),
  listSizes: (versionId: string) =>
    request<GarmentSize[]>(`/api/v1/garment-model-versions/${versionId}/sizes`),
  listFabrics: () => request<FabricConfiguration[]>("/api/v1/fabric-configurations"),
  listTables: () => request<CuttingTableConfiguration[]>("/api/v1/cutting-table-configurations"),
  createOrder: (payload: unknown) =>
    request<ProductionOrder>("/api/v1/production-orders", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getOrder: (orderId: string) => request<ProductionOrder>(`/api/v1/production-orders/${orderId}`),
  listOrders: (page = 1) => request<Page<ProductionOrder>>(`/api/v1/production-orders?page=${page}&page_size=20`),
  listPatternSets: () => request<PatternSet[]>("/api/v1/pattern-sets"),
  getPatternSet: (patternSetId: string, sizeCode: string) =>
    request<PatternSet>(`/api/v1/pattern-sets/${patternSetId}?size_code=${sizeCode}`),
  getPatternPieceGeometry: (pieceId: string) =>
    request<PatternPieceGeometry>(`/api/v1/pattern-pieces/${pieceId}/geometry`),
  previewMarker: (payload: unknown) =>
    request<MarkerPreview>("/api/v1/geometry/markers/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  createOptimizationRun: (orderId: string, payload: unknown, idempotencyKey: string) =>
    request<OptimizationRun>(`/api/v1/production-orders/${orderId}/optimization-runs`, {
      method: "POST", body: JSON.stringify(payload), headers: { "Idempotency-Key": idempotencyKey },
    }),
  getOptimizationRun: (runId: string) => request<OptimizationRun>(`/api/v1/optimization-runs/${runId}`),
  listOrderRuns: (orderId: string, page = 1) => request<Page<OptimizationRun>>(`/api/v1/production-orders/${orderId}/optimization-runs?page=${page}&page_size=20`),
  retryOptimizationRun: (runId: string) => request<OptimizationRun>(`/api/v1/optimization-runs/${runId}/retry`, { method: "POST" }),
  cancelOptimizationRun: (runId: string) => request<OptimizationRun>(`/api/v1/optimization-runs/${runId}/cancel`, { method: "POST" }),
  useCurrentOptimizationPlan: (runId: string) => request<OptimizationRun>(`/api/v1/optimization-runs/${runId}/use-current-plan`, { method: "POST" }),
  listOptimizationSolutions: (runId: string) => request<OptimizationSolutionSummary[]>(`/api/v1/optimization-runs/${runId}/solutions`),
  getOptimizationSolution: (solutionId: string) => request<OptimizationSolution>(`/api/v1/optimization-solutions/${solutionId}`),
  listSolutionSpreads: (solutionId: string) => request<Spread[]>(`/api/v1/optimization-solutions/${solutionId}/spreads`),
  getMarkerArtifact: (markerHash: string) => request<MarkerArtifact>(`/api/v1/markers/${markerHash}`),
  getOptimizationAudit: (runId: string) => request<Record<string, unknown>>(`/api/v1/optimization-runs/${runId}/audit`),
  exportOptimizationRun: (runId: string) => request<Record<string, unknown>>(`/api/v1/optimization-runs/${runId}/export`),
  markerSvgUrl: (markerHash: string) => `${API_URL}/api/v1/markers/${markerHash}/svg`,
};
