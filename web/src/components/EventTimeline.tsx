import { CheckCircle2, CircleAlert, Clock3, SearchCheck } from "lucide-react";

import type { UnifiedEvent } from "../lib/presentation";
import { formatTime, stepMeaning, toneForStatus } from "../lib/presentation";
import { Badge, EmptyState } from "./ui";

export function EventTimeline({ events }: { events: UnifiedEvent[] }) {
  if (events.length === 0) {
    return <EmptyState title="No workflow has executed" description="Start a payment. Exact backend events will appear here in timestamp order." />;
  }
  return (
    <div className="event-timeline">
      {events.map((event, index) => {
        const tone = toneForStatus(event.status);
        const Icon = tone === "danger" ? CircleAlert : tone === "warning" ? Clock3 : tone === "success" ? CheckCircle2 : SearchCheck;
        return (
          <article className={`timeline-row timeline-${tone}`} key={event.key} id={event.eventId ? `event-${event.eventId}` : undefined}>
            <div className="timeline-index">{index + 1}</div>
            <div className="timeline-rail"><span><Icon size={15} /></span></div>
            <div className="timeline-content">
              <div className="timeline-title"><div><strong>{event.source} · {event.action.replaceAll("_", " ")}</strong><span>{formatTime(event.timestamp)}{event.attempt ? ` · attempt ${event.attempt}` : ""}</span></div><Badge tone={tone === "danger" ? "danger" : tone === "warning" ? "warning" : tone === "success" ? "success" : "neutral"}>{event.status}</Badge></div>
              <p>{event.detail}</p>
              <div className="event-meaning"><strong>What this means:</strong> {stepMeaning(event.action, event.detail)}</div>
              {(event.eventId || event.requestId || event.traceId) && <div className="event-ids">{event.eventId && <span>event <code>{event.eventId}</code></span>}{event.requestId && <span>request <code>{event.requestId}</code></span>}{event.traceId && <span>trace <code>{event.traceId}</code></span>}</div>}
            </div>
          </article>
        );
      })}
    </div>
  );
}
