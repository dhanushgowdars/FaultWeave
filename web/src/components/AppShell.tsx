import {
  Activity,
  BarChart3,
  Boxes,
  ChevronRight,
  CircleDot,
  FlaskConical,
  LayoutDashboard,
  Menu,
  ShieldCheck,
  X,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import type { ConnectionState } from "../api/types";

const navigation = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/live", label: "Live Demo", icon: Activity },
  { to: "/cases", label: "Case Evidence", icon: CircleDot },
  { to: "/evaluation", label: "Evaluation", icon: BarChart3 },
  { to: "/system", label: "System", icon: Boxes },
];

const connectionLabels: Record<ConnectionState, string> = {
  connecting: "Connecting",
  connected: "Live stream connected",
  reconnecting: "Reconnecting",
  offline: "Backend unavailable",
};

export function AppShell({
  children,
  connection,
}: {
  children: ReactNode;
  connection: ConnectionState;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="app-shell">
      <aside className={`sidebar ${open ? "sidebar--open" : ""}`}>
        <div className="brand">
          <span className="brand__mark" aria-hidden="true"><FlaskConical size={22} /></span>
          <div><strong>FaultWeave</strong><small>Operational intelligence</small></div>
        </div>
        <button className="sidebar__close" onClick={() => setOpen(false)} aria-label="Close navigation"><X /></button>
        <nav className="sidebar__nav" aria-label="Primary navigation">
          {navigation.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} onClick={() => setOpen(false)}>
              <Icon size={19} /> <span>{label}</span> <ChevronRight className="nav-chevron" size={16} />
            </NavLink>
          ))}
        </nav>
        <div className="sidebar__footer">
          <div className="research-badge"><ShieldCheck size={17} /><span><strong>Frozen research models</strong><small>No runtime retraining</small></span></div>
          <p>Controlled transaction microservice environment. No real-money processing.</p>
        </div>
      </aside>
      {open && <button className="sidebar-backdrop" onClick={() => setOpen(false)} aria-label="Close navigation" />}
      <div className="app-shell__content">
        <header className="topbar">
          <button className="menu-button" onClick={() => setOpen(true)} aria-label="Open navigation"><Menu /></button>
          <div className={`connection connection--${connection}`}><span />{connectionLabels[connection]}</div>
          <div className="topbar__context"><span>Research environment</span><strong>Phase 15</strong></div>
        </header>
        <main>{children}</main>
      </div>
    </div>
  );
}
