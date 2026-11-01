import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, type Health } from "./api";
import { useAsync } from "./lib/hooks";
import OverviewPage from "./pages/Overview";
import ModelsPage from "./pages/Models";
import SuitesPage from "./pages/Suites";
import NewRunPage from "./pages/NewRun";
import RunDetailPage from "./pages/RunDetail";
import ReviewPage from "./pages/Review";
import ReportPage from "./pages/Report";
import HistoryPage from "./pages/History";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/models", label: "Models" },
  { to: "/suites", label: "Suites" },
  { to: "/runs/new", label: "New run" },
  { to: "/review", label: "Review" },
  { to: "/report", label: "Gap report" },
  { to: "/history", label: "History" },
];

function OllamaStatus() {
  const { data } = useAsync<Health>(() => api.get("/health"), [], {
    intervalMs: 15000,
    while: () => true,
  });
  if (!data) return null;
  const ok = data.ollama.reachable;
  return (
    <div className="flex items-center gap-2 text-xs text-ink-400" aria-live="polite">
      <span className={`h-2 w-2 rounded-full ${ok ? "bg-emerald-400" : "bg-rose-400"}`} aria-hidden />
      {ok ? `Ollama online · ${data.ollama.models.length} model(s)` : "Ollama offline"}
    </div>
  );
}

export default function App() {
  const [open, setOpen] = useState(false);
  return (
    <div className="min-h-screen md:flex">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-indigo-500 focus:px-3 focus:py-1.5">
        Skip to content
      </a>
      <aside className="border-b border-ink-800 bg-ink-900 md:sticky md:top-0 md:h-screen md:w-56 md:shrink-0 md:border-b-0 md:border-r">
        <div className="flex items-center justify-between px-4 py-3">
          <div className="flex items-center gap-2">
            <img src="/favicon.svg" alt="" className="h-6 w-6" />
            <span className="text-sm font-semibold tracking-wide">Arbiter</span>
            <span className="text-[10px] uppercase tracking-wider text-ink-500">eval lab</span>
          </div>
          <button
            className="rounded px-2 py-1 text-sm text-ink-300 md:hidden"
            aria-expanded={open}
            aria-controls="main-nav"
            onClick={() => setOpen((o) => !o)}
          >
            Menu
          </button>
        </div>
        <nav id="main-nav" aria-label="Main" className={`${open ? "block" : "hidden"} px-2 pb-3 md:block`}>
          <ul className="space-y-0.5">
            {NAV.map((n) => (
              <li key={n.to}>
                <NavLink
                  to={n.to}
                  end={n.end}
                  onClick={() => setOpen(false)}
                  className={({ isActive }) =>
                    `block rounded-md px-3 py-1.5 text-sm ${isActive ? "bg-indigo-500/15 text-indigo-200" : "text-ink-300 hover:bg-ink-800 hover:text-ink-100"}`
                  }
                >
                  {n.label}
                </NavLink>
              </li>
            ))}
          </ul>
          <div className="mt-4 border-t border-ink-800 px-3 pt-3">
            <OllamaStatus />
            <p className="mt-2 text-[11px] leading-snug text-ink-500">
              Arbiter tests and compares models. It does not train them.
            </p>
          </div>
        </nav>
      </aside>
      <main id="main" className="min-w-0 flex-1 px-4 py-6 md:px-8">
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/suites" element={<SuitesPage />} />
          <Route path="/runs/new" element={<NewRunPage />} />
          <Route path="/runs/:id" element={<RunDetailPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/report" element={<ReportPage />} />
          <Route path="/report/:id" element={<ReportPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="*" element={<p className="text-ink-400">Page not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}
