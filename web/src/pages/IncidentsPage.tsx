import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Database, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { PageHeader, QueryState, StatusPill } from "../components/ui";
import { formatLabel, formatTime } from "../lib/format";

export function IncidentsPage() {
  const [query, setQuery] = useState("");
  const result = useQuery({
    queryKey: ["demo-cases"],
    queryFn: api.demoCases,
    refetchInterval: 5_000,
    retry: false,
  });
  const cases = useMemo(() => {
    const value = query.toLowerCase().trim();
    return (result.data?.cases ?? []).filter(
      (item) =>
        !value ||
        [
          item.case_id,
          item.fault_id,
          item.first_failure?.service,
          item.request_id,
        ].some((field) =>
          String(field ?? "")
            .toLowerCase()
            .includes(value),
        ),
    );
  }, [query, result.data]);

  return (
    <div className="page-container">
      <PageHeader
        eyebrow="Persistent investigation history"
        title="Engineer review queue and saved cases"
        description="Every row is a real controlled transaction with its request ID, trace ID, ordered evidence, engineer decision and separate recovery result stored in SQLite."
      />
      <div className="persistence-banner">
        <Database size={20} />
        <div>
          <strong>Durable local evidence</strong>
          <p>
            Cases remain available after refreshing the dashboard or restarting
            the frontend.
          </p>
        </div>
        <StatusPill tone="success">SQLite</StatusPill>
      </div>
      <div className="toolbar">
        <label className="search-field">
          <Search size={17} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search case, request, fault or service"
          />
        </label>
        <StatusPill tone="neutral">
          {result.data?.count ?? 0} saved cases
        </StatusPill>
      </div>
      <section className="panel incident-list-panel">
        {result.isLoading ? (
          <QueryState
            kind="loading"
            title="Loading saved cases"
            message="Reading the local case evidence database."
          />
        ) : result.isError ? (
          <QueryState
            kind="error"
            title="Case database unavailable"
            message="Start the local demo controller on port 18116."
          />
        ) : cases.length ? (
          <div className="incident-table-wrap">
            <table className="incident-table">
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Scenario</th>
                  <th>First broken step</th>
                  <th>Customer result</th>
                  <th>Recovery</th>
                  <th>Started</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {cases.map((item) => (
                  <tr key={item.case_id}>
                    <td>
                      <strong>{item.case_id}</strong>
                      <small className="mono">{item.request_id}</small>
                    </td>
                    <td>
                      <strong>{item.scenario.title}</strong>
                      <small>{formatLabel(item.fault_id)}</small>
                    </td>
                    <td>
                      {item.first_failure ? (
                        <>
                          <strong>
                            {formatLabel(item.first_failure.service)}
                          </strong>
                          <small>{item.first_failure.message}</small>
                        </>
                      ) : (
                        "No failure captured"
                      )}
                    </td>
                    <td>
                      <StatusPill
                        tone={
                          item.transaction_result?.succeeded
                            ? "success"
                            : item.transaction_result
                              ? "danger"
                              : "warning"
                        }
                      >
                        {item.transaction_result
                          ? `HTTP ${item.transaction_result.status_code ?? "ERR"}`
                          : "Running"}
                      </StatusPill>
                    </td>
                    <td>
                      <StatusPill
                        tone={item.recovery_verified ? "success" : "warning"}
                      >
                        {item.status === "AWAITING_REVIEW"
                          ? "Review required"
                          : item.status === "REMEDIATING"
                            ? "In progress"
                            : item.status === "ABORTED"
                              ? "Safety abort"
                              : item.recovery_verified
                                ? "Verified"
                                : "Pending"}
                      </StatusPill>
                    </td>
                    <td>{formatTime(item.started_at)}</td>
                    <td>
                      <Link
                        className="icon-link"
                        to={`/cases/${item.case_id}`}
                        aria-label={`Open ${item.case_id}`}
                      >
                        <ArrowRight size={18} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <QueryState
            kind="empty"
            title="No cases stored"
            message="Run a controlled transaction from Live Demo to create the first evidence package."
          />
        )}
      </section>
    </div>
  );
}
