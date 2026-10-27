/** Load only the destination's code when a user points at or focuses a link. */
export const pageLoaders = {
  DashboardPage: () => import("@/pages/Dashboard").then(m => ({ default: m.DashboardPage })),
  HomeBoardPage: () => import("@/pages/HomeBoard").then(m => ({ default: m.HomeBoardPage })),
  TopicsPage: () => import("@/pages/Topics").then(m => ({ default: m.TopicsPage })),
  PublishQueuePage: () => import("@/pages/PublishQueue").then(m => ({ default: m.PublishQueuePage })),
  JobsListPage: () => import("@/pages/JobsList").then(m => ({ default: m.JobsListPage })),
  JobDetailPage: () => import("@/pages/JobDetail").then(m => ({ default: m.JobDetailPage })),
  ReportsPage: () => import("@/pages/Reports").then(m => ({ default: m.ReportsPage })),
  ReportViewPage: () => import("@/pages/ReportView").then(m => ({ default: m.ReportViewPage })),
  SettingsPage: () => import("@/pages/Settings").then(m => ({ default: m.SettingsPage })),
  SchedulerPage: () => import("@/pages/Scheduler").then(m => ({ default: m.SchedulerPage })),
  RoundsPage: () => import("@/pages/Rounds").then(m => ({ default: m.RoundsPage })),
  AskPage: () => import("@/pages/Ask").then(m => ({ default: m.AskPage })),
  ShareViewPage: () => import("@/pages/ShareView").then(m => ({ default: m.ShareViewPage })),
  VideoScriptsPage: () => import("@/pages/VideoScripts").then(m => ({ default: m.VideoScriptsPage })),
  VideoScriptViewPage: () => import("@/pages/VideoScriptView").then(m => ({ default: m.VideoScriptViewPage })),
  VideoRecipesPage: () => import("@/pages/VideoRecipes").then(m => ({ default: m.VideoRecipesPage })),
  MatricesPage: () => import("@/pages/Matrices").then(m => ({ default: m.MatricesPage })),
  MatrixDetailPage: () => import("@/pages/Matrices").then(m => ({ default: m.MatrixDetailPage })),
  MatrixJobVideoPage: () => import("@/pages/Matrices").then(m => ({ default: m.MatrixJobVideoPage })),
  SourcesPage: () => import("@/pages/Sources").then(m => ({ default: m.SourcesPage })),
  OutputsPage: () => import("@/pages/Outputs").then(m => ({ default: m.OutputsPage })),
  PlatformsPage: () => import("@/pages/Platforms").then(m => ({ default: m.PlatformsPage })),
};

export function pageLoader(path: string) {
  if (path === "/") return pageLoaders.HomeBoardPage;
  if (path === "/engine") return pageLoaders.DashboardPage;
  if (path === "/matrices") return pageLoaders.MatricesPage;
  if (/^\/matrices\/[^/]+\/video\/[^/]+$/.test(path)) return pageLoaders.MatrixJobVideoPage;
  if (/^\/matrices\/[^/]+$/.test(path)) return pageLoaders.MatrixDetailPage;
  if (path === "/sources") return pageLoaders.SourcesPage;
  if (path === "/outputs") return pageLoaders.OutputsPage;
  if (path === "/jobs") return pageLoaders.JobsListPage;
  if (/^\/jobs\/.+/.test(path)) return pageLoaders.JobDetailPage;
  if (path === "/rounds") return pageLoaders.RoundsPage;
  if (path === "/ask") return pageLoaders.AskPage;
  if (path === "/reports") return pageLoaders.ReportsPage;
  if (path === "/reports/view") return pageLoaders.ReportViewPage;
  if (path === "/topics") return pageLoaders.TopicsPage;
  if (path === "/publish") return pageLoaders.PublishQueuePage;
  if (path === "/platforms") return pageLoaders.PlatformsPage;
  if (path === "/video") return pageLoaders.VideoScriptsPage;
  if (/^\/video\/[^/]+$/.test(path)) return pageLoaders.VideoScriptViewPage;
  if (/^\/video-recipes(?:\/.*)?$/.test(path)) return pageLoaders.VideoRecipesPage;
  if (path === "/settings") return pageLoaders.SettingsPage;
  if (path === "/scheduler") return pageLoaders.SchedulerPage;
}

const warmed = new Set<unknown>();
export function preloadPage(path: string) {
  const load = pageLoader(path);
  if (!load || warmed.has(load)) return;
  warmed.add(load);
  void load().catch(() => warmed.delete(load));
}
