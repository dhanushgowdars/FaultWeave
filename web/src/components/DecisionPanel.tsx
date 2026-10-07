import { Activity, CircleAlert, MapPin, ShieldCheck } from "lucide-react";
import type { Incident } from "../api/types";
import { formatLabel, formatPercent } from "../lib/format";
import { StatusPill } from "./ui";

export function DecisionPanel({ incident }: { incident: Incident | null | undefined }) {
  if (!incident) {
    return (
      <div className="decision decision--healthy">
        <div className="decision__icon"><ShieldCheck size={26} /></div>
        <div>
          <p className="eyebrow">Current decision</p>
          <h2>Normal operation</h2>
          <p>No active abnormal pattern is confirmed by the live intelligence pipeline.</p>
        </div>
        <StatusPill tone="success">Healthy</StatusPill>
      </div>
    );
  }

  const classification = incident.classification;
  const unknown = classification?.accepted_as_known === false;
  const label = unknown ? "Unknown abnormal pattern" : formatLabel(classification?.label);
  const origin = formatLabel(incident.localization?.probable_origin);
  return (
    <div className={`decision ${unknown ? "decision--unknown" : "decision--incident"}`}>
      <div className="decision__icon"><CircleAlert size={26} /></div>
      <div className="decision__main">
        <p className="eyebrow">Current decision</p>
        <h2>{label}</h2>
        <p>
          {unknown
            ? "The signal is abnormal but falls outside the accepted support of known fault classes."
            : "The abnormal signal matches a verified known operational fault pattern."}
        </p>
        <div className="decision__facts">
          <span><MapPin size={15} /> Probable origin: <strong>{origin}</strong></span>
          <span><Activity size={15} /> Confidence: <strong>{formatPercent(classification?.confidence)}</strong></span>
        </div>
      </div>
      <StatusPill tone={unknown ? "unknown" : "danger"}>{incident.severity?.level ?? "Investigating"}</StatusPill>
    </div>
  );
}
