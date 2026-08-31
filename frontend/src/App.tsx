import { NavLink, Route, HashRouter, Routes } from "react-router-dom";
import { AnalysisProvider } from "./context/AnalysisContext";
import CareerGraphPage from "./pages/CareerGraph";
import NewCvPage from "./pages/NewCv";
import RequirementsUploadPage from "./pages/RequirementsUpload";
import MatchReviewPage from "./pages/MatchReview";
import CvContextPage from "./pages/CvContext";
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
              <NavItem to="/requirements" label="2 Claude" />
              <NavItem to="/match-review" label="3 Neo4j" />
              <NavItem to="/cv-context" label="4 Claude" />
              <NavItem to="/cv-preview" label="Debug" />
            </nav>
          </header>
          <main className="app-main">
            <Routes>
              <Route path="/" element={<CareerGraphPage />} />
              <Route path="/new-cv" element={<NewCvPage />} />
              <Route path="/requirements" element={<RequirementsUploadPage />} />
              <Route path="/match-review" element={<MatchReviewPage />} />
              <Route path="/cv-context" element={<CvContextPage />} />
              <Route path="/cv-preview" element={<CvPreviewPage />} />
            </Routes>
          </main>
        </div>
      </HashRouter>
    </AnalysisProvider>
  );
}
