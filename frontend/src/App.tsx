import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
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
import Albums from "./pages/Albums";
import AlbumPage from "./pages/AlbumPage";
import PublicAlbum from "./pages/PublicAlbum";
import Upload from "./pages/Upload";
import People from "./pages/People";
import PersonPage from "./pages/PersonPage";
import FaceReview from "./pages/FaceReview";

export default function App() {
  const location = useLocation();
  // Public share links (/s/<token>) never need an account.
  if (location.pathname.startsWith("/s/")) {
    return (
      <Routes>
        <Route path="/s/:token" element={<PublicAlbum />} />
      </Routes>
    );
  }
  return <SignedInApp />;
}

function SignedInApp() {
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
    { to: "/upload", label: "Upload", icon: Icons.upload },
    { to: "/browse", label: "Browse", icon: Icons.browse },
    { to: "/albums", label: "Albums", icon: Icons.albums },
    { to: "/people", label: "People", icon: Icons.people },
    ...(cfg.map_tile_url ? [{ to: "/map", label: "Map", icon: Icons.map }] : []),
    { to: "/audit", label: "Audit log", icon: Icons.audit },
    { to: "/settings", label: "Settings", icon: Icons.settings },
  ];

  // Narrow screens: the sidebar becomes a drawer (button, or swipe in from the left edge).
  const location = useLocation();
  const [drawer, setDrawer] = useState(false);
  const touch = useRef<{ x: number; y: number; fromEdge: boolean } | null>(null);
  useEffect(() => setDrawer(false), [location.pathname, location.search]);
  useEffect(() => {
    const narrow = () => window.matchMedia("(max-width: 1023px)").matches;
    const start = (e: TouchEvent) => {
      const t = e.touches[0];
      touch.current = { x: t.clientX, y: t.clientY, fromEdge: t.clientX < 24 };
    };
    const end = (e: TouchEvent) => {
      const s = touch.current;
      touch.current = null;
      if (!s || !narrow() || document.querySelector(".viewer-overlay, .overlay")) return;
      const t = e.changedTouches[0];
      const dx = t.clientX - s.x;
      const dy = t.clientY - s.y;
      if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
      if (dx > 0 && s.fromEdge) setDrawer(true);
      else if (dx < 0) setDrawer(false);
    };
    window.addEventListener("touchstart", start, { passive: true });
    window.addEventListener("touchend", end, { passive: true });
    return () => {
      window.removeEventListener("touchstart", start);
      window.removeEventListener("touchend", end);
    };
  }, []);
  const current = links.find((l) => (l.end ? location.pathname === l.to : location.pathname.startsWith(l.to)));

  const signOut = async () => {
    await post("/api/auth/logout");
    qc.clear();
    qc.setQueryData(["me"], null);
    window.location.href = "/";
  };

  return (
    <div className="app">
      <header className="mobile-bar">
        <button className="btn ghost menu-btn" onClick={() => setDrawer(true)} aria-label="Open menu" aria-expanded={drawer}>
          <span className="burger" />
          {pending.data?.count ? <span className="nav-badge dot">{pending.data.count}</span> : null}
        </button>
        <strong className="mobile-title">{current?.label ?? ""}</strong>
      </header>
      {drawer && <div className="drawer-backdrop" onClick={() => setDrawer(false)} aria-hidden="true" />}
      <nav className={"sidebar" + (drawer ? " open" : "")} aria-label="Main">
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
          <Route path="/upload" element={<Upload />} />
          <Route path="/browse" element={<Browse />} />
          <Route path="/albums" element={<Albums />} />
          <Route path="/people" element={<People />} />
          <Route path="/people/review" element={<FaceReview />} />
          <Route path="/people/:id" element={<PersonPage me={me} />} />
          <Route path="/albums/:id" element={<AlbumPage me={me} />} />
          {cfg.map_tile_url && <Route path="/map" element={<MapPage />} />}
          <Route path="/audit" element={<Audit />} />
          <Route path="/settings" element={<SettingsPage me={me} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
