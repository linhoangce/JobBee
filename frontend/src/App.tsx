import { Link, NavLink, Route, Routes } from "react-router-dom";
import Approvals from "./pages/Approvals";
import Home from "./pages/Home";
import Jobs from "./pages/Jobs";
import Onboarding from "./pages/Onboarding";
import Settings from "./pages/Settings";
import Tracker from "./pages/Tracker";

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          <span className="brand-mark">JB</span>
          <span className="brand-text">JobBee</span>
        </Link>
        <nav className="nav">
          <NavLink to="/" end>
            Home
          </NavLink>
          <NavLink to="/onboarding">Onboarding</NavLink>
          <NavLink to="/jobs">Jobs</NavLink>
          <NavLink to="/approvals">Approvals</NavLink>
          <NavLink to="/tracker">Apply</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
      </header>
      <main className="main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/onboarding" element={<Onboarding />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/tracker" element={<Tracker />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
      <footer className="footer">
        <span>JobBee · job search milestone</span>
      </footer>
    </div>
  );
}
