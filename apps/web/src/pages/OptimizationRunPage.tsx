import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import type { OptimizationSolution, OptimizationSolutionSummary, Spread } from "../types";
import { MarkerCanvas } from "./NestingLabPage";

const PHASES: Record<string, string> = {
  GENERATING_CANDIDATES: "Generando composiciones candidatas",
  NESTING: "Evaluando marcadores geométricos",
  PLANNING: "Planificando capas y tendidos",
  VALIDATING: "Validando el plan completo",
  FINALIZING: "Finalizando auditoría",
};
const PROFILE_LABELS: Record<string, string> = { MIN_FABRIC: "Menor tela", MIN_SPREADS: "Menos tendidos", BALANCED: "Balanceada" };

function formatMeters(value: unknown) { return Number(value ?? 0).toLocaleString("es-CO", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }

function SpreadCard({ spread }: { spread: Spread }) {
  const [open, setOpen] = useState(false);
  const marker = useQuery({ queryKey: ["marker", spread.marker_hash], queryFn: () => api.getMarkerArtifact(spread.marker_hash), enabled: open });
  return <article className="spread-card">
    <button className="spread-heading" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      <span><small>TENDIDO {spread.sequence}</small><strong>{Object.entries(spread.composition).map(([size, quantity]) => `${size} × ${quantity}`).join(" + ")}</strong></span>
      <span><b>{spread.layers} capas</b><small>{spread.repeats > 1 ? `repetir ${spread.repeats}×` : "1 ejecución"}</small></span>
      <span><b>{spread.marker_length_cm.toFixed(2)} cm</b><small>{spread.fabric_consumption_m.toFixed(2)} m consumidos</small></span>
      <i>{open ? "−" : "+"}</i>
    </button>
    {open && <div className="spread-detail">
      <dl><div><dt>Producción</dt><dd>{Object.entries(spread.production_by_size).map(([size, value]) => `${size} ${value}`).join(" · ")}</dd></div><div><dt>Marker</dt><dd><code>{spread.marker_hash}</code></dd></div><div><dt>Validación</dt><dd>{spread.validation_status}</dd></div></dl>
      {marker.isLoading && <div className="loading-card">Cargando marcador certificado…</div>}
      {marker.data && <MarkerCanvas marker={marker.data.marker} layers={{ ids: true, bbox: false, grain: true, clearance: false, debug: false }} />}
    </div>}
  </article>;
}

function Comparison({ solutions, selected, onSelect }: { solutions: OptimizationSolutionSummary[]; selected: string; onSelect: (id: string) => void }) {
  return <section className="solution-comparison"><h2>Comparar alternativas</h2><div className="comparison-grid">
    {solutions.map((solution) => <button key={solution.id} className={selected === solution.id ? "solution-option selected" : "solution-option"} onClick={() => onSelect(solution.id)}>
      <span>{solution.profiles.map((profile) => PROFILE_LABELS[profile] ?? profile).join(" · ")}</span>
      <strong>{formatMeters(solution.metrics.total_linear_consumption_m)} m</strong>
      <small>{String(solution.metrics.spread_count)} tendidos · {String(solution.metrics.total_overproduction)} extra</small>
      <em>{solution.planning_optimality}</em>
    </button>)}
  </div></section>;
}

function Results({ runId, summaries }: { runId: string; summaries: OptimizationSolutionSummary[] }) {
  const [selectedId, setSelectedId] = useState(summaries[0]?.id ?? "");
  useEffect(() => { if (!selectedId && summaries[0]) setSelectedId(summaries[0].id); }, [selectedId, summaries]);
  const selected = useQuery({ queryKey: ["solution", selectedId], queryFn: () => api.getOptimizationSolution(selectedId), enabled: Boolean(selectedId) });
  const audit = useQuery({ queryKey: ["run-audit", runId], queryFn: () => api.getOptimizationAudit(runId) });
  const solution = selected.data;
  return <>
    <header className="results-hero"><div><p className="eyebrow">Solución recomendada</p><h1>Plan de corte validado</h1><p className="intro">Planificación óptima sobre el catálogo de marcadores evaluado. Los marcadores individuales son soluciones heurísticas validadas.</p></div>{solution && <div className="recommendation-metric"><span>Tela lineal</span><strong>{formatMeters(solution.metrics.total_linear_consumption_m)} m</strong></div>}</header>
    <Comparison solutions={summaries} selected={selectedId} onSelect={setSelectedId} />
    {selected.isLoading && <div className="loading-card">Cargando solución…</div>}
    {solution && <SolutionDetail solution={solution} />}
    <details className="audit-panel"><summary>Auditoría técnica</summary><pre>{JSON.stringify(audit.data ?? {}, null, 2)}</pre></details>
  </>;
}

function SolutionDetail({ solution }: { solution: OptimizationSolution }) {
  return <div className="solution-detail">
    <section className="result-kpis"><div><span>Tendidos</span><strong>{String(solution.metrics.spread_count)}</strong></div><div><span>Producción</span><strong>{Object.values(solution.metrics.produced_by_size as Record<string, number>).reduce((a, b) => a + b, 0)}</strong></div><div><span>Sobreproducción</span><strong>{String(solution.metrics.total_overproduction)}</strong></div><div><span>Eficiencia global</span><strong>{Number(solution.metrics.global_efficiency_percentage).toFixed(2)}%</strong></div></section>
    <section className="why-card"><p className="eyebrow">¿Por qué se recomienda esta opción?</p><p>{solution.explanation}</p></section>
    <section className="size-results"><h2>Producción por talla</h2><table><thead><tr><th>Talla</th><th>Pedido</th><th>Producción</th><th>Extra</th></tr></thead><tbody>{solution.size_results.map((row) => <tr key={row.size_code} className={row.overproduction ? "has-extra" : ""}><th>{row.size_code}</th><td>{row.requested}</td><td>{row.produced}</td><td>{row.overproduction ? `+${row.overproduction}` : "0"}</td></tr>)}</tbody></table></section>
    <section className="spread-list"><div className="section-heading"><div><p className="eyebrow">Ejecución</p><h2>Detalle de tendidos</h2></div><span>{solution.validation.status}</span></div>{solution.spreads.map((spread) => <SpreadCard key={spread.id} spread={spread} />)}</section>
  </div>;
}

export function OptimizationRunPage() {
  const { runId = "" } = useParams();
  const run = useQuery({ queryKey: ["optimization-run", runId], queryFn: () => api.getOptimizationRun(runId), enabled: Boolean(runId), refetchInterval: (query) => ["QUEUED", "RUNNING"].includes(query.state.data?.status ?? "") ? 1500 : false });
  const solutions = useQuery({ queryKey: ["optimization-solutions", runId], queryFn: () => api.listOptimizationSolutions(runId), enabled: run.data?.status === "SUCCEEDED" });
  const cancel = useMutation({ mutationFn: () => api.cancelOptimizationRun(runId), onSuccess: () => run.refetch() });
  if (run.isLoading) return <main className="workspace"><div className="loading-card">Consultando corrida…</div></main>;
  if (run.error || !run.data) return <main className="workspace"><div className="load-error">No fue posible consultar la optimización.</div></main>;
  if (run.data.status === "SUCCEEDED") return <main className="workspace results-workspace"><Results runId={runId} summaries={solutions.data ?? []} /></main>;
  if (["FAILED", "CANCELLED", "TIMED_OUT", "INFEASIBLE"].includes(run.data.status)) return <main className="workspace"><div className="prepared-card"><p className="eyebrow">Corrida finalizada</p><h1>{run.data.status}</h1><p className="intro">{run.data.error_detail ?? "La corrida se detuvo sin publicar soluciones."}</p></div></main>;
  const progress = run.data.progress; const total = Math.max(progress.candidates_generated, progress.candidates_evaluated + progress.candidates_pending);
  return <main className="workspace processing-workspace"><section className="processing-card">
    <div className="processing-mark"><span></span></div><p className="eyebrow">Production planning · Fase 2E</p><h1>Optimizando corte…</h1><p className="intro">{PHASES[run.data.phase] ?? run.data.phase}</p>
    <div className="progress-track"><span style={{ width: total ? `${Math.min(100, progress.candidates_evaluated / total * 100)}%` : "8%" }} /></div>
    <dl className="processing-stats"><div><dt>Candidates</dt><dd>{progress.candidates_evaluated} / {total || "—"}</dd></div><div><dt>Mejor solución válida</dt><dd>{progress.best_feasible_found ? "Encontrada" : "Buscando"}</dd></div><div><dt>Markers factibles</dt><dd>{progress.candidates_feasible}</dd></div><div><dt>Tiempo</dt><dd>{Math.round((run.data.elapsed.geometry_ms + run.data.elapsed.planner_ms) / 1000)} s</dd></div></dl>
    <button className="secondary-button cancel-button" onClick={() => cancel.mutate()} disabled={cancel.isPending || run.data.cancel_requested}>{run.data.cancel_requested ? "Cancelación solicitada" : "Cancelar"}</button>
    <code className="run-id">Run {run.data.id}</code>
  </section></main>;
}
