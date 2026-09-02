import { Link, Route, Routes } from "react-router-dom";
import { NewOrderPage } from "./pages/NewOrderPage";
import { OrderPreparedPage } from "./pages/OrderPreparedPage";
import { PatternInspectionPage } from "./pages/PatternInspectionPage";
import { NestingLabPage } from "./pages/NestingLabPage";
import { OptimizationRunPage } from "./pages/OptimizationRunPage";

export default function App() {
  return (
    <div className="app-shell">
      <nav className="topbar" aria-label="Navegación principal">
        <Link to="/" className="brand"><span className="brand-mark" aria-hidden="true">CO</span><span>Costura Óptima</span></Link>
        <div className="topbar-links"><Link to="/patterns">Patrones técnicos</Link><Link to="/nesting-lab">Nesting Lab</Link><span className="product-state">Preparación de órdenes</span></div>
      </nav>
      <Routes>
        <Route path="/" element={<NewOrderPage />} />
        <Route path="/orders/:orderId" element={<OrderPreparedPage />} />
        <Route path="/patterns" element={<PatternInspectionPage />} />
        <Route path="/nesting-lab" element={<NestingLabPage />} />
        <Route path="/optimization-runs/:runId" element={<OptimizationRunPage />} />
      </Routes>
    </div>
  );
}
