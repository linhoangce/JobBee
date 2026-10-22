import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api, type SetupStatus } from "./api";
import ErrorBoundary from "./components/ErrorBoundary";
import { BeeMark } from "./components/Icon";
import { ToastProvider } from "./components/Toast";
import Home from "./pages/Home";
import Settings from "./pages/Settings";
import Welcome from "./pages/Welcome";

export default function App() {
  const [setup, setSetup] = useState<SetupStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const location = useLocation();

  const loadSetup = useCallback(async () => {
    try {
      setSetup(await api.setup());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void loadSetup();
  }, [loadSetup]);

  if (setup == null) {
    return (
      <div className="welcome" style={{ textAlign: "center" }}>
        <div className="welcome-brand">
          <BeeMark />
          JobBee
        </div>
        {error ? (
          <div className="stack" style={{ alignItems: "center" }}>
            <div className="notice bad">{error}</div>
            <button onClick={() => void loadSetup()}>Try again</button>
          </div>
        ) : (
          <div className="spinner" style={{ margin: "0 auto" }} />
        )}
      </div>
    );
  }

  const onWelcome = location.pathname.startsWith("/welcome");
  if (!setup.onboarded && !onWelcome) return <Navigate to="/welcome" replace />;

  return (
    <ToastProvider>
      {onWelcome ? (
        <Routes>
          <Route path="/welcome" element={<Welcome setup={setup} onDone={loadSetup} />} />
        </Routes>
      ) : (
        <div className="app">
          <header className="topbar">
            <div className="topbar-inner">
              <Link to="/" className="brand">
                <BeeMark />
                <span className="brand-text">JobBee</span>
              </Link>
              <nav className="nav">
                <NavLink to="/" end>
                  Home
                </NavLink>
                <NavLink to="/settings">Settings</NavLink>
              </nav>
            </div>
          </header>
          <main className="main">
            <ErrorBoundary key={location.pathname} label="This page">
              <Routes>
                <Route path="/" element={<Home />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </ErrorBoundary>
          </main>
        </div>
      )}
    </ToastProvider>
  );
}
