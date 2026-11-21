import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError, post, type Me } from "./api";
import { Brand, Icons, Loading, useAppConfig } from "./ui";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Review from "./pages/Review";
import Browse from "./pages/Browse";
import MapPage from "./pages/MapPage";
import Pending from "./pages/Pending";
import Quarantine from "./pages/Quarantine";
import Audit from "./pages/Audit";
import SettingsPage from "./pages/Settings";

export default function App() {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<Me>("/api/auth/me"),
    retry: (n, err) => !(err instanceof ApiError && err.status === 401) && n < 2,
  });

  if (me.isLoading) return <Loading what="Starting" />;
  if (!me.data) return <Login />;
  return <Shell me={me.data} />;
}

function Shell({ me }: { me: Me }) {
  const qc = useQueryClient();
  const cfg = useAppConfig();
  const pending = useQuery({
    queryKey: ["pending-count"],
    queryFn: () => api<{ count: number }>("/api/pending/count"),
    refetchInterval: 30_000,
  });

  const links = [
    { to: "/", label: "Overview", icon: Icons.overview, end: true },
    { to: "/review", label: "Review", icon: Icons.review },
    { to: "/pending", label: "Approvals", icon: Icons.pending, badge: pending.data?.count },
    { to: "/quarantine", label: "Quarantine", icon: Icons.quarantine },
    { to: "/browse", label: "Browse", icon: Icons.browse },
    ...(cfg.map_tile_url ? [{ to: "/map", label: "Map", icon: Icons.map }] : []),
    { to: "/audit", label: "Audit log", icon: Icons.audit },
    { to: "/settings", label: "Settings", icon: Icons.settings },
  ];

  const signOut = async () => {
    await post("/api/auth/logout");
    qc.clear();
    qc.setQueryData(["me"], null);
    window.location.href = "/";
  };

  return (
    <div className="app">
      <nav className="sidebar" aria-label="Main">
        <Brand />
        {links.map((l) => (
          <NavLink key={l.to} to={l.to} end={l.end} className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
            {l.icon}
            {l.label}
            {l.badge ? <span className="nav-badge">{l.badge}</span> : null}
          </NavLink>
        ))}
        <div className="sidebar-foot">
          <div>
            <div style={{ fontWeight: 600 }}>
              {me.name} {me.is_admin && <span className="badge accent">Admin</span>}
            </div>
            <div className="small faint">{me.home_folder ? `Folder: ${me.home_folder}` : "No folder"}</div>
          </div>
          <button className="btn sm" onClick={signOut}>
            Sign out
          </button>
        </div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/review" element={<Review me={me} />} />
          <Route path="/pending" element={<Pending me={me} />} />
          <Route path="/quarantine" element={<Quarantine />} />
          <Route path="/browse" element={<Browse />} />
          {cfg.map_tile_url && <Route path="/map" element={<MapPage />} />}
          <Route path="/audit" element={<Audit />} />
          <Route path="/settings" element={<SettingsPage me={me} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
