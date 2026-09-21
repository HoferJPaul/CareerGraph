import { NavLink, Route, HashRouter, Routes } from "react-router-dom";
import { AnalysisProvider } from "./context/AnalysisContext";
import CareerGraphPage from "./pages/CareerGraph";
import NewCvPage from "./pages/NewCv";
import MatchReviewPage from "./pages/MatchReview";
import CvPreviewPage from "./pages/CvPreview";

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink to={to} className={({ isActive }) => (isActive ? "active" : undefined)} end>
      {label}
    </NavLink>
  );
}

export default function App() {
  return (
    <AnalysisProvider>
      <HashRouter>
        <div className="app-shell">
          <header className="app-header">
            <div className="brand">
              <span className="brand-mark">
                Career<span>Graph</span>
              </span>
              <span className="brand-tagline">The LLM writes. The graph proves.</span>
            </div>
            <nav className="app-nav">
              <NavItem to="/" label="CareerGraph" />
              <NavItem to="/new-cv" label="1 Job" />
              <NavItem to="/match-review" label="2 Match" />
              <NavItem to="/cv" label="3 CV" />
            </nav>
          </header>
          <main className="app-main">
            <Routes>
              <Route path="/" element={<CareerGraphPage />} />
              <Route path="/new-cv" element={<NewCvPage />} />
              <Route path="/match-review" element={<MatchReviewPage />} />
              <Route path="/cv" element={<CvPreviewPage />} />
            </Routes>
          </main>
        </div>
      </HashRouter>
    </AnalysisProvider>
  );
}
