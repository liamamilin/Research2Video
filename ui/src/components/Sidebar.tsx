import { useState } from "react";
import { NavLink } from "react-router-dom";
import { ListOrdered, FileText, LayoutDashboard, Settings, Clock, LogOut, Radar, MessageSquareText, Clapperboard, Gauge, Lightbulb, Send, BookOpen, Boxes, Layers, Rss, PanelsTopLeft } from "lucide-react";
import { useAuthStore } from "@/lib/auth-store";
import { cn } from "@/lib/utils";
import { AccountModal } from "./AccountModal";

// 矩阵是入口：Job 跑出报告，报告按视频配置写成文稿；产出按 矩阵 › Job › 报告 看（spec/product-redesign.md）
const mainItems = [
  { to: "/matrices", label: "矩阵", icon: Boxes },
  { to: "/outputs", label: "产出", icon: Layers },
  { to: "/", label: "视频看板", icon: LayoutDashboard },
  { to: "/video", label: "文稿", icon: Clapperboard },
  { to: "/publish", label: "成片与发布", icon: Send },
  { to: "/platforms", label: "平台管理", icon: PanelsTopLeft },
  { to: "/ask", label: "助手", icon: MessageSquareText },
];

const engineItems = [
  { to: "/engine", label: "研究总览", icon: Gauge },
  { to: "/jobs", label: "Jobs", icon: ListOrdered },
  { to: "/rounds", label: "情报轮次", icon: Radar },
  { to: "/reports", label: "报告", icon: FileText },
];

// 不经过视频配置、直接挑选题和改配方：仍然能用，只是不再是必经的一步
const advancedItems = [
  { to: "/topics", label: "选题", icon: Lightbulb },
  { to: "/sources", label: "信息源", icon: Rss },
  { to: "/video-recipes", label: "文稿配方", icon: BookOpen },
];

const adminEngineItems = [{ to: "/scheduler", label: "周期调度", icon: Clock }];
const adminItems = [{ to: "/settings", label: "设置", icon: Settings }];

export function Sidebar({ open, onNavigate }: { open: boolean; onNavigate?: () => void }) {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const [accountOpen, setAccountOpen] = useState(false);

  const admin = user?.role === "admin";
  const groups = [
    { title: "", items: mainItems },
    { title: "研究引擎", items: admin ? [...engineItems, ...adminEngineItems] : engineItems },
    { title: "高级", items: advancedItems },
    { title: "", items: admin ? adminItems : [] },
  ].filter((g) => g.items.length);

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-30 flex flex-col bg-bg-card border-r border-border transition-transform w-56",
        open ? "translate-x-0" : "-translate-x-full"
      )}
    >
      <div className="p-4 border-b border-border">
        <h2 className="font-semibold text-sm">AI Research Console</h2>
        {user && (
          <button
            onClick={() => setAccountOpen(true)}
            className="text-xs text-text-muted hover:text-text mt-1 text-left min-h-[24px] py-0.5"
          >
            {user.username} · {user.role} <span className="opacity-60">（账号设置）</span>
          </button>
        )}
      </div>

      <nav className="flex-1 p-2 space-y-1 overflow-y-auto">
        {groups.map((group, gi) => (
          <div key={gi} className={cn("space-y-1", gi > 0 && "pt-2")}>
            {group.title ? <div className="px-3 pt-1 text-xs text-text-muted">{group.title}</div> : null}
            {group.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                onClick={onNavigate}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-2 px-3 py-2 rounded-md text-sm transition",
                    isActive
                      ? "bg-accent-solid text-white"
                      : "text-text-muted hover:text-text hover:bg-bg-hover"
                  )
                }
              >
                <item.icon className="w-4 h-4" />
                {item.label}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <AccountModal open={accountOpen} onClose={() => setAccountOpen(false)} />

      <div className="p-2 border-t border-border">
        <button
          onClick={() => logout()}
          className="flex items-center gap-2 w-full px-3 py-2 rounded-md text-sm text-text-muted hover:text-text hover:bg-bg-hover transition"
        >
          <LogOut className="w-4 h-4" />
          退出
        </button>
      </div>
    </aside>
  );
}
