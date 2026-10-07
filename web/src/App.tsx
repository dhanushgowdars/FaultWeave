import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { useIntelligenceStream } from "./hooks/useIntelligenceStream";
import { EvaluationPage } from "./pages/EvaluationPage";
import { IncidentDetailPage } from "./pages/IncidentDetailPage";
import { IncidentsPage } from "./pages/IncidentsPage";
import { LiveDemoPage } from "./pages/LiveDemoPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SystemPage } from "./pages/SystemPage";

export default function App() {
  const stream = useIntelligenceStream();
  return (
    <AppShell connection={stream.connection}>
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/live" element={<LiveDemoPage />} />
        <Route path="/cases" element={<IncidentsPage />} />
        <Route path="/cases/:caseId" element={<IncidentDetailPage />} />
        <Route path="/incidents" element={<Navigate to="/cases" replace />} />
        <Route path="/evaluation" element={<EvaluationPage />} />
        <Route path="/system" element={<SystemPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
