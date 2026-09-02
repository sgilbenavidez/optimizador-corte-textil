export type Measurement = {
  measurement_code: string;
  measurement_kind: string;
  value: number;
  unit: string;
  provenance_status: string;
  source_reference: string;
  derivation: string | null;
  version: string;
};

export type GarmentSize = {
  id: string;
  code: string;
  label: string;
  display_order: number;
  measurements: Measurement[];
};

export type GarmentVersion = {
  id: string;
  model_code: string;
  version_code: string;
  display_name: string;
  market_profile: string;
  lifecycle_status: string;
  validation_status: string;
  is_orderable: boolean;
  unit: string;
  content_hash: string;
  provenance: Record<string, unknown>;
  warning: string | null;
  sizes: GarmentSize[];
};

export type GarmentModel = {
  id: string;
  code: string;
  display_name: string;
  garment_type_code: string;
  versions: GarmentVersion[];
};

export type FabricConfiguration = {
  id: string;
  version_code: string;
  display_name: string;
  physical_width_cm: number;
  usable_width_cm: number;
  fabric_family: string;
  directional: boolean;
  lay_mode: string;
  piece_clearance_cm: number;
  left_margin_cm: number;
  right_margin_cm: number;
  start_margin_cm: number;
  end_margin_cm: number;
  content_hash: string;
};

export type CuttingTableConfiguration = {
  id: string;
  version_code: string;
  display_name: string;
  physical_length_cm: number;
  usable_length_cm: number;
  max_layers: number;
  content_hash: string;
};

export type ProductionOrder = {
  id: string;
  status: string;
  garment_model_version_id: string;
  pattern_set_version_id: string | null;
  fabric_configuration_id: string;
  cutting_table_configuration_id: string;
  total_quantity: number;
  demand: Array<{ size_code: string; quantity: number }>;
  garment_model: GarmentVersion;
  fabric_configuration: FabricConfiguration;
  cutting_table_configuration: CuttingTableConfiguration;
  optimization_policy: Record<string, unknown>;
  snapshot_hash: string;
  created_at: string;
};

export type PatternPieceSummary = {
  id: string;
  size_code: string;
  piece_code: "FRONT" | "BACK" | "SLEEVE" | "NECKBAND";
  quantity: number;
  grainline: { kind: string; unit: string; start: [number, number]; end: [number, number] };
  allowed_rotations_degrees: number[];
  mirror_allowed: boolean;
  edge_allowances_cm: Record<string, number>;
  technical_measurements: Record<string, number>;
  geometry_metrics: {
    seamline: GeometryMetrics;
    cutline: GeometryMetrics;
  };
  geometry_hash: string;
};

export type GeometryMetrics = {
  valid: boolean;
  validity_reason: string;
  area_cm2: number;
  perimeter_cm: number;
  bbox_cm: { min_x: number; min_y: number; max_x: number; max_y: number };
};

export type PatternSet = {
  id: string;
  garment_model_version_id: string;
  parameter_profile_id: string;
  version: number;
  version_code: string;
  algorithm_version: string;
  measurement_snapshot_hash: string;
  content_hash: string;
  lifecycle_status: "ENGINEERING";
  validation_status: "UNVALIDATED_FOR_PRODUCTION";
  unit: "cm";
  geometry_units_per_cm: number;
  warning: string;
  created_at: string;
  pieces: PatternPieceSummary[];
};

export type PatternPieceGeometry = PatternPieceSummary & {
  pattern_set_version_id: string;
  source_geometry: {
    type: string;
    closed: boolean;
    segments: Array<{
      kind: "LINE" | "CUBIC_BEZIER";
      start: [number, number];
      end: [number, number];
      control1?: [number, number];
      control2?: [number, number];
      edge: string;
    }>;
  };
  seamline_geometry: { coordinates: [Array<[number, number]>] };
  cutline_geometry: { coordinates: [Array<[number, number]>] };
  operational_geometry: { units_per_cm: number; coordinates: [Array<[number, number]>] };
};

export type MarkerPlacement = {
  piece_instance_id: string;
  pattern_piece_id: string;
  size_code: string;
  piece_code: string;
  transform: { rotation: number; mirrored: boolean };
  translation: { x_units: number; y_units: number; x_cm: number; y_cm: number };
  transformed_polygon: { unit: string; coordinates: [Array<[number, number]>] };
  grainline: { unit: string; start: [number, number]; end: [number, number] };
  bbox: { units: [number, number, number, number]; cm: [number, number, number, number] };
  geometry_hash: string;
  sequence: number;
};

export type MarkerPreview = {
  status: string;
  search_status: string;
  warning: string;
  marker_length_cm: number | null;
  usable_width_cm: number;
  physical_width_cm: number;
  piece_count: number;
  placements: MarkerPlacement[];
  piece_area_total_cm2: number;
  marker_area_cm2: number | null;
  waste_area_cm2: number | null;
  efficiency_percentage: number | null;
  waste_percentage: number | null;
  lower_bound_length_cm: number;
  area_lower_bound_cm: number;
  gap_to_area_lower_bound_cm: number | null;
  validation: { status: string; checks: Record<string, boolean>; errors: string[]; pair_checks: number };
  algorithm: string;
  algorithm_version: string;
  nfp_status: string;
  seed: number;
  piece_order_strategy: string;
  candidate_order: string;
  transform_order: number[];
  evaluation_count: number;
  stopping_reason: string;
  elapsed_time_ms: number;
  input_hash: string;
  result_hash: string;
  cache: { hits: number; misses: number };
  diagnostics: string[];
  margins_cm: { left: number; right: number; start: number; end: number };
  clearance_cm: number;
  max_length_cm: number;
  debug_geometry: { candidate_positions?: Array<[number, number]>; rejected_placements?: Array<{ position: [number, number]; reason: string }> } | null;
};

export type OptimizationRun = {
  id: string;
  production_order_id: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED" | "TIMED_OUT";
  phase: string;
  input_hash: string;
  configuration: Record<string, unknown>;
  cancel_requested: boolean;
  progress: {
    candidates_generated: number; candidates_evaluated: number; candidates_pending: number;
    candidates_feasible: number; candidates_infeasible: number; candidates_not_evaluated: number;
    best_feasible_found: boolean;
  };
  elapsed: { candidate_generation_ms: number; geometry_ms: number; planner_ms: number; total_ms: number };
  solution_count: number;
  error_detail: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type OptimizationSolutionSummary = {
  id: string; solution_hash: string; profiles: string[]; rank: number; planning_status: string;
  planning_optimality: string; solution_origin: string; metrics: Record<string, number | Record<string, number>>;
  validation: { status: string; checks: Record<string, boolean>; errors: string[] }; explanation: string;
};

export type Spread = {
  id: string; solution_id: string; marker_hash: string; spread_hash: string; sequence: number;
  layers: number; repeats: number; composition: Record<string, number>; production_by_size: Record<string, number>;
  marker_length_cm: number; fabric_consumption_m: number; marker_efficiency_percentage: number;
  marker_search_status: string; validation_status: string;
};

export type OptimizationSolution = OptimizationSolutionSummary & {
  run_id: string;
  size_results: Array<{ size_code: string; requested: number; produced: number; overproduction: number }>;
  spreads: Spread[];
};

export type MarkerArtifact = {
  marker_hash: string; composition: Record<string, number>; geometry_engine_version: string;
  marker_search_status: string; validation_status: string; marker: MarkerPreview & { geometry_units_per_cm: number };
};
