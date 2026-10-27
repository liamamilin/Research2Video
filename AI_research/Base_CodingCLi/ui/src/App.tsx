import { lazy, Suspense, type ReactElement } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

import { AuthProvider } from "@/components/AuthProvider";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Layout } from "@/components/Layout";
import { LoginPage } from "@/pages/Login";
import { useAuthStore } from "@/lib/auth-store";
import { ToastProvider } from "@/lib/toast";
import { pageLoaders } from "@/lib/page-modules";
import { PageLoading } from "@/components/PageLoading";

const DashboardPage = lazy(pageLoaders.DashboardPage);
const HomeBoardPage = lazy(pageLoaders.HomeBoardPage);
const TopicsPage = lazy(pageLoaders.TopicsPage);
const PublishQueuePage = lazy(pageLoaders.PublishQueuePage);
const JobsListPage = lazy(pageLoaders.JobsListPage);
const JobDetailPage = lazy(pageLoaders.JobDetailPage);
const ReportsPage = lazy(pageLoaders.ReportsPage);
const ReportViewPage = lazy(pageLoaders.ReportViewPage);
const SettingsPage = lazy(pageLoaders.SettingsPage);
const SchedulerPage = lazy(pageLoaders.SchedulerPage);
const RoundsPage = lazy(pageLoaders.RoundsPage);
const AskPage = lazy(pageLoaders.AskPage);
const ShareViewPage = lazy(pageLoaders.ShareViewPage);
const VideoScriptsPage = lazy(pageLoaders.VideoScriptsPage);
const VideoScriptViewPage = lazy(pageLoaders.VideoScriptViewPage);
const VideoRecipesPage = lazy(pageLoaders.VideoRecipesPage);
const MatricesPage = lazy(pageLoaders.MatricesPage);
const MatrixDetailPage = lazy(pageLoaders.MatrixDetailPage);
const MatrixJobVideoPage = lazy(pageLoaders.MatrixJobVideoPage);
const SourcesPage = lazy(pageLoaders.SourcesPage);
const OutputsPage = lazy(pageLoaders.OutputsPage);
const PlatformsPage = lazy(pageLoaders.PlatformsPage);

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
