import {
  BookOpenCheck,
  ClipboardList,
  FolderGit2,
  Gauge,
  LayoutDashboard,
  Menu,
  MessageSquareText,
  Monitor,
  Moon,
  Sun,
  ThumbsUp,
  Users,
  X,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useCurrentProject, useHealth } from "../hooks/queries";
import { useAppState, type ThemeChoice } from "../state/AppState";
import { LearnerPicker } from "./LearnerPicker";
import { cx } from "./ui";

const NAV = [
  { to: "/projects", label: "Projects", icon: FolderGit2 },
  { to: "/overview", label: "Overview", icon: LayoutDashboard },
  { to: "/chat", label: "Chat", icon: MessageSquareText },
  { to: "/onboarding", label: "Onboarding", icon: BookOpenCheck },
  { to: "/manager", label: "Manager", icon: Users, manager: true },
  { to: "/feedback", label: "Feedback", icon: ThumbsUp },
  { to: "/evaluation", label: "Evaluation", icon: Gauge },
];

function Brand() {
  return (
    <div className="flex items-center gap-2 px-3 py-4">
      <img src="/favicon.svg" alt="" className="size-7" />
      <div>
        <div className="text-sm font-semibold leading-tight text-ink">RepoGuide</div>
        <div className="text-[11px] leading-tight text-muted">Local codebase guide</div>
      </div>
    </div>
  );
}

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const { managerView } = useAppState();
  return (
    <nav className="flex flex-col gap-0.5 px-2" aria-label="Main">
      {NAV.filter((item) => !item.manager || managerView).map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          onClick={onNavigate}
          className={({ isActive }) =>
            cx(
              "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm",
              isActive ? "bg-accent-soft font-medium text-accent-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
            )
          }
        >
          <Icon className="size-4" aria-hidden />
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

function OllamaStatus() {
  const health = useHealth();
  const data = health.data;
  let tone = "bg-muted";
  let text = "Checking Ollama…";
  if (health.isError) {
    tone = "bg-[var(--status-critical)]";
    text = "Backend not reachable";
  } else if (data) {
    const ok = data.ollama.reachable && data.ollama.chat_model_available && data.ollama.embed_model_available;
    tone = ok ? "bg-[var(--status-good)]" : "bg-[var(--status-critical)]";
    text = !data.ollama.reachable
      ? "Ollama not running"
      : ok
        ? `${data.config.chat_model} · ${data.config.embed_model}`
        : "Model missing: run ollama pull";
  }
  return (
    <div className="flex items-center gap-2 px-3 py-3 text-[11px] text-muted" title={data?.ollama.error ?? undefined}>
      <span className={cx("inline-block size-2 shrink-0 rounded-full", tone)} aria-hidden />
      <span className="truncate">{text}</span>
    </div>
  );
}

function ProjectSwitcher() {
  const { setProjectId } = useAppState();
  const { project, projects } = useCurrentProject();
  const list = projects.data ?? [];
  if (!list.length) {
    // Only claim there are no projects when the list actually loaded.
    const text = projects.isError ? "Projects unavailable" : projects.isPending ? "Loading projects…" : "No projects yet";
    return <span className="text-sm text-muted">{text}</span>;
  }
  return (
    <label className="flex min-w-0 items-center gap-2 text-sm">
      <span className="hidden text-muted sm:inline">Project</span>
      <select
        className="h-8 min-w-0 max-w-[14rem] rounded-md border border-line-strong bg-surface px-2 text-sm text-ink"
        value={project?.id ?? ""}
        onChange={(event) => setProjectId(Number(event.target.value))}
        aria-label="Current project"
      >
        {list.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function ThemeToggle() {
  const { theme, setTheme } = useAppState();
  const order: ThemeChoice[] = ["system", "light", "dark"];
  const next = order[(order.indexOf(theme) + 1) % order.length];
  const Icon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;
  return (
    <button
      className="rounded-md p-2 text-ink-2 hover:bg-surface-2"
      onClick={() => setTheme(next)}
      title={`Theme: ${theme} (switch to ${next})`}
      aria-label={`Theme: ${theme}. Switch to ${next}.`}
    >
      <Icon className="size-4" />
    </button>
  );
}

function ManagerToggle() {
  const { managerView, setManagerView } = useAppState();
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm text-ink-2" title="Show the manager dashboard">
      <ClipboardList className="hidden size-4 sm:block" aria-hidden />
      <span className="hidden sm:inline">Manager view</span>
      <button
        type="button"
        role="switch"
        aria-checked={managerView}
        aria-label="Manager view"
        onClick={() => setManagerView(!managerView)}
        className={cx(
          "relative h-5 w-9 rounded-full transition-colors",
          managerView ? "bg-accent" : "bg-line-strong",
        )}
      >
        <span
          className={cx(
            "absolute top-0.5 size-4 rounded-full bg-white shadow transition-transform",
            managerView ? "translate-x-4" : "translate-x-0.5",
          )}
        />
      </button>
    </label>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex h-full">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-line bg-surface lg:flex">
        <Brand />
        <Nav />
        <div className="mt-auto border-t border-line">
          <OllamaStatus />
        </div>
      </aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col bg-surface shadow-xl">
            <div className="flex items-center justify-between pr-2">
              <Brand />
              <button className="rounded p-2 text-muted hover:bg-surface-2" onClick={() => setOpen(false)} aria-label="Close menu">
                <X className="size-4" />
              </button>
            </div>
            <Nav onNavigate={() => setOpen(false)} />
            <div className="mt-auto border-t border-line">
              <OllamaStatus />
            </div>
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-surface/95 px-4 py-2 backdrop-blur">
          <button className="rounded p-1.5 text-ink-2 hover:bg-surface-2 lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu">
            <Menu className="size-5" />
          </button>
          <ProjectSwitcher />
          <div className="ml-auto flex flex-wrap items-center gap-3">
            <LearnerPicker />
            <ManagerToggle />
            <ThemeToggle />
          </div>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-6xl px-4 py-6">{children}</div>
        </main>
      </div>
    </div>
  );
}
