import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, CircleDashed, WifiOff } from "lucide-react";

export function StatusPill({
  tone = "neutral",
  children,
}: {
  tone?: "success" | "warning" | "danger" | "info" | "unknown" | "neutral";
  children: ReactNode;
}) {
  return <span className={`status-pill status-pill--${tone}`}>{children}</span>;
}

export function MetricCard({
  eyebrow,
  value,
  detail,
  accent = "blue",
}: {
  eyebrow: string;
  value: ReactNode;
  detail: string;
  accent?: "blue" | "violet" | "green" | "amber";
}) {
  return (
    <article className={`metric-card metric-card--${accent}`}>
      <p className="eyebrow">{eyebrow}</p>
      <div className="metric-card__value">{value}</div>
      <p className="muted">{detail}</p>
    </article>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <p className="eyebrow eyebrow--blue">{eyebrow}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </header>
  );
}

export function Panel({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel__header">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function QueryState({
  kind,
  title,
  message,
}: {
  kind: "loading" | "error" | "empty";
  title: string;
  message: string;
}) {
  const Icon = kind === "loading" ? CircleDashed : kind === "error" ? WifiOff : AlertTriangle;
  return (
    <div className={`query-state query-state--${kind}`}>
      <Icon size={22} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
    </div>
  );
}

export function EvidenceCheck({ children }: { children: ReactNode }) {
  return (
    <li className="evidence-check">
      <CheckCircle2 size={17} aria-hidden="true" />
      <span>{children}</span>
    </li>
  );
}
