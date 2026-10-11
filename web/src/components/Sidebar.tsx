import {
  Activity,
  BrainCircuit,
  ClipboardCheck,
  FileSearch,
  FlaskConical,
  LayoutDashboard,
  LogOut,
} from "lucide-react";

import type { PageName } from "../api/types";

const items: Array<{ page: PageName; label: string; icon: typeof Activity }> = [
  { page: "overview", label: "System overview", icon: LayoutDashboard },
  { page: "demo", label: "Live demonstration", icon: Activity },
  { page: "investigation", label: "Investigation", icon: FileSearch },
  { page: "evidence", label: "Case evidence", icon: ClipboardCheck },
  { page: "intelligence", label: "Intelligence", icon: BrainCircuit },
  { page: "evaluation", label: "Evaluation", icon: FlaskConical },
];

export function Sidebar({
  page,
  onNavigate,
  onLogout,
  caseCount,
}: {
  page: PageName;
  onNavigate: (page: PageName) => void;
  onLogout: () => void;
  caseCount: number;
}) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark"><span /><span /><span /></div>
        <div><strong>FaultWeave</strong><small>Command Center</small></div>
      </div>
      <div className="nav-label">Demonstration</div>
      <nav>
        {items.map(({ page: itemPage, label, icon: Icon }) => {
          const requiresCase = ["investigation", "evidence", "intelligence"].includes(itemPage);
          const disabled = requiresCase && caseCount === 0;
          return (
          <button
            key={itemPage}
            className={page === itemPage ? "active" : ""}
            onClick={() => onNavigate(itemPage)}
            disabled={disabled}
            title={disabled ? "Run the first workflow to unlock this page" : undefined}
          >
            <Icon size={18} strokeWidth={1.8} />
            <span>{label}</span>
            {itemPage === "evidence" && caseCount > 0 && <b className="nav-count">{caseCount}</b>}
          </button>
          );
        })}
      </nav>
      <div className="sidebar-trust">
        <span className="trust-dot" />
        <div><strong>Evidence-first</strong><small>No automatic recovery</small></div>
      </div>
      <button className="logout-button" onClick={onLogout}>
        <LogOut size={16} /> End engineer session
      </button>
    </aside>
  );
}
