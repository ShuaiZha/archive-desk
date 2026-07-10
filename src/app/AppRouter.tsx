import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { LoadingPage } from "../pages/SystemPages";
import { useArchiveDesk } from "./model";

const ExportPage = lazy(() => import("../pages/ExportPage").then((module) => ({ default: module.ExportPage })));
const JobPage = lazy(() => import("../pages/JobPage").then((module) => ({ default: module.JobPage })));
const JobsPage = lazy(() => import("../pages/JobsPage").then((module) => ({ default: module.JobsPage })));
const LoginPage = lazy(() => import("../pages/LoginPage").then((module) => ({ default: module.LoginPage })));
const SettingsPage = lazy(() => import("../pages/SettingsPage").then((module) => ({ default: module.SettingsPage })));
const SetupPage = lazy(() => import("../pages/SetupPage").then((module) => ({ default: module.SetupPage })));

function HomeRedirect() {
  const model = useArchiveDesk();
  if (!model.credentialsConfigured) return <Navigate to="/setup" replace />;
  if (model.accounts.length === 0) return <Navigate to="/login" replace />;
  return <Navigate to="/export" replace />;
}

function RequireCredentials({ children }: { children: ReactNode }) {
  const { credentialsConfigured } = useArchiveDesk();
  return credentialsConfigured ? children : <Navigate to="/setup" replace />;
}

function RequireAccount({ children }: { children: ReactNode }) {
  const model = useArchiveDesk();
  if (!model.credentialsConfigured) return <Navigate to="/setup" replace />;
  if (model.accounts.length === 0) return <Navigate to="/login" replace />;
  return children;
}

export function AppRouter() {
  return (
    <Suspense fallback={<LoadingPage />}>
      <Routes>
        <Route path="/" element={<HomeRedirect />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/login" element={<RequireCredentials><LoginPage /></RequireCredentials>} />
        <Route path="/export" element={<RequireAccount><ExportPage /></RequireAccount>} />
        <Route path="/jobs" element={<RequireAccount><JobsPage /></RequireAccount>} />
        <Route path="/jobs/:jobId" element={<RequireAccount><JobPage /></RequireAccount>} />
        <Route path="/settings" element={<RequireCredentials><SettingsPage /></RequireCredentials>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
