import { lazy, Suspense, type ReactElement } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

import { AuthProvider } from "@/components/AuthProvider";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Layout } from "@/components/Layout";
import { LoginPage } from "@/pages/Login";
import { useAuthStore } from "@/lib/auth-store";
import { ToastProvider } from "@/lib/toast";
import { PageLoading } from "@/components/PageLoading";

const DashboardPage = lazy(() => import("@/pages/Dashboard").then(m => ({ default: m.DashboardPage })));
const HomeBoardPage = lazy(() => import("@/pages/HomeBoard").then(m => ({ default: m.HomeBoardPage })));
const TopicsPage = lazy(() => import("@/pages/Topics").then(m => ({ default: m.TopicsPage })));
const PublishQueuePage = lazy(() => import("@/pages/PublishQueue").then(m => ({ default: m.PublishQueuePage })));
const JobsListPage = lazy(() => import("@/pages/JobsList").then(m => ({ default: m.JobsListPage })));
const JobDetailPage = lazy(() => import("@/pages/JobDetail").then(m => ({ default: m.JobDetailPage })));
const ReportsPage = lazy(() => import("@/pages/Reports").then(m => ({ default: m.ReportsPage })));
const ReportViewPage = lazy(() => import("@/pages/ReportView").then(m => ({ default: m.ReportViewPage })));
const SettingsPage = lazy(() => import("@/pages/Settings").then(m => ({ default: m.SettingsPage })));
const SchedulerPage = lazy(() => import("@/pages/Scheduler").then(m => ({ default: m.SchedulerPage })));
const RoundsPage = lazy(() => import("@/pages/Rounds").then(m => ({ default: m.RoundsPage })));
const AskPage = lazy(() => import("@/pages/Ask").then(m => ({ default: m.AskPage })));
const ShareViewPage = lazy(() => import("@/pages/ShareView").then(m => ({ default: m.ShareViewPage })));
const VideoScriptsPage = lazy(() => import("@/pages/VideoScripts").then(m => ({ default: m.VideoScriptsPage })));
const VideoScriptViewPage = lazy(() => import("@/pages/VideoScriptView").then(m => ({ default: m.VideoScriptViewPage })));
const VideoRecipesPage = lazy(() => import("@/pages/VideoRecipes").then(m => ({ default: m.VideoRecipesPage })));
const MatricesPage = lazy(() => import("@/pages/Matrices").then(m => ({ default: m.MatricesPage })));
const MatrixDetailPage = lazy(() => import("@/pages/Matrices").then(m => ({ default: m.MatrixDetailPage })));
const MatrixJobVideoPage = lazy(() => import("@/pages/Matrices").then(m => ({ default: m.MatrixJobVideoPage })));
const SourcesPage = lazy(() => import("@/pages/Sources").then(m => ({ default: m.SourcesPage })));
const OutputsPage = lazy(() => import("@/pages/Outputs").then(m => ({ default: m.OutputsPage })));
const PlatformsPage = lazy(() => import("@/pages/Platforms").then(m => ({ default: m.PlatformsPage })));

/** Blocks deep links to admin-only pages instead of letting every action 403. */
export function AdminOnly({ children }: { children: ReactElement }) {
  const role = useAuthStore((s) => s.user?.role);
  if (role !== "admin") {
    return (
      <div className="card p-8 text-center space-y-3">
        <div className="text-sm text-danger">403 — 该页面仅限管理员访问</div>
        <div className="text-xs text-text-muted">
          你的角色是 {role || "未知"}。请使用管理员账号登录，或从侧栏选择可用页面。
        </div>
        <a href="/" className="btn text-xs">返回总览</a>
      </div>
    );
  }
  return children;
}

function AppRoutes() {
  const user = useAuthStore((s) => s.user);

  if (!user) {
    return (
      <Routes>
        <Route path="/share/:token" element={<ShareViewPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="/share/:token" element={<ShareViewPage />} />
      <Route element={<Layout />}>
        <Route index element={<HomeBoardPage />} />
        <Route path="/engine" element={<DashboardPage />} />
        <Route path="/matrices" element={<MatricesPage />} />
        <Route path="/sources" element={<SourcesPage />} />
        <Route path="/matrices/:key" element={<MatrixDetailPage />} />
        <Route path="/matrices/:key/video/:job" element={<MatrixJobVideoPage />} />
        <Route path="/outputs" element={<OutputsPage />} />
        <Route path="/jobs" element={<JobsListPage />} />
        <Route path="/jobs/:name" element={<JobDetailPage />} />
        <Route path="/rounds" element={<RoundsPage />} />
        <Route path="/ask" element={<AskPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/reports/view" element={<ReportViewPage />} />
        <Route path="/topics" element={<TopicsPage />} />
        <Route path="/publish" element={<PublishQueuePage />} />
        <Route path="/platforms" element={<PlatformsPage />} />
        <Route path="/video" element={<VideoScriptsPage />} />
        <Route path="/video-recipes" element={<VideoRecipesPage />} />
        <Route path="/video-recipes/:key" element={<VideoRecipesPage />} />
        <Route path="/video-recipes/parts/:kind/:name" element={<VideoRecipesPage />} />
        <Route path="/video/:id" element={<VideoScriptViewPage />} />
        <Route
          path="/settings"
          element={<AdminOnly><SettingsPage /></AdminOnly>}
        />
        <Route
          path="/scheduler"
          element={<AdminOnly><SchedulerPage /></AdminOnly>}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ErrorBoundary>
        <ToastProvider>
          <AuthProvider>
            <Suspense fallback={<PageLoading />}><AppRoutes /></Suspense>
          </AuthProvider>
        </ToastProvider>
      </ErrorBoundary>
    </BrowserRouter>
  );
}
