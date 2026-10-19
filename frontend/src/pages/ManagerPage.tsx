import { useQuery } from "@tanstack/react-query";
import { ChevronRight, MessageCircleQuestion, Users } from "lucide-react";
import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Card, EmptyState, ErrorBox, Meter, PageHeader, Spinner, StatTile, formatDate, pct } from "../components/ui";
import { useCurrentProject } from "../hooks/queries";
import { useAppState } from "../state/AppState";

export default function ManagerPage() {
  const { managerView, setManagerView } = useAppState();
  const { project } = useCurrentProject();
  const [scope, setScope] = useState<"project" | "all">("project");
  const projectId = scope === "project" ? project?.id ?? null : null;
  const dashboard = useQuery({
    queryKey: ["dashboard", projectId],
    queryFn: () => api.dashboard(projectId),
    enabled: managerView,
    refetchInterval: 15000,
  });
  const [expanded, setExpanded] = useState<number | null>(null);

  if (!managerView) {
    return (
      <EmptyState
        icon={<Users className="size-8" />}
        title="Manager view is off"
        action={<button className="text-sm font-medium text-accent-ink underline" onClick={() => setManagerView(true)}>Turn on manager view</button>}
      >
        The dashboard shows every learner's progress, quiz scores and the questions they ask most.
      </EmptyState>
    );
  }

  const data = dashboard.data;
  const learners = data?.learners ?? [];
  const active = learners.filter((l) => l.paths.length || l.questions_asked);
  const progressValues = active.flatMap((l) => l.paths.map((p) => p.progress));
  const quizValues = active.map((l) => l.avg_quiz_score).filter((v): v is number => v !== null);
  const avg = (values: number[]) => (values.length ? values.reduce((a, b) => a + b, 0) / values.length : null);
  const maxCount = Math.max(1, ...(data?.top_questions ?? []).map((q) => q.count));

  return (
    <div className="space-y-5">
      <PageHeader
        title="Manager dashboard"
        description="Learner progress, quiz scores and the questions people ask most."
        actions={
          <label className="flex items-center gap-2 text-sm text-ink-2">
            Scope
            <select className="h-8 rounded-md border border-line-strong bg-surface px-2 text-sm" value={scope}
              onChange={(event) => setScope(event.target.value as "project" | "all")}>
              <option value="project">{project ? project.name : "Current project"}</option>
              <option value="all">All projects</option>
            </select>
          </label>
        }
      />
      {dashboard.isLoading && <Spinner label="Loading dashboard…" />}
      {dashboard.error && <ErrorBox error={dashboard.error} onRetry={() => dashboard.refetch()} />}
      {data && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Active learners" value={active.length} sub={`${learners.length} registered`} />
            <StatTile label="Average path progress" value={pct(avg(progressValues))} />
            <StatTile label="Average quiz score" value={pct(avg(quizValues))} />
            <StatTile label="Questions asked" value={active.reduce((s, l) => s + l.questions_asked, 0)} />
          </div>
          <Card title="Learners" padded={false}>
            {learners.length === 0 ? (
              <div className="p-4">
                <EmptyState title="No learners yet">
                  Learners appear once someone picks a name in the top bar and starts a path or asks a question.
                </EmptyState>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[40rem] text-sm">
                  <thead>
                    <tr className="border-b border-line text-left text-xs text-muted">
                      <th className="px-4 py-2 font-medium">Learner</th>
                      <th className="px-4 py-2 font-medium">Paths</th>
                      <th className="w-48 px-4 py-2 font-medium">Steps completed</th>
                      <th className="px-4 py-2 text-right font-medium">Avg quiz</th>
                      <th className="px-4 py-2 text-right font-medium">Questions</th>
                      <th className="px-4 py-2 font-medium">Last activity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {learners.map((learner) => {
                      const open = expanded === learner.id;
                      const ratio = learner.steps_total ? learner.steps_completed / learner.steps_total : 0;
                      return (
                        <Fragment key={learner.id}>
                          <tr className="border-b border-line last:border-0 hover:bg-surface-2">
                            <td className="px-4 py-2">
                              <button className="flex items-center gap-1 font-medium text-ink" onClick={() => setExpanded(open ? null : learner.id)}
                                aria-expanded={open} disabled={!learner.paths.length}>
                                <ChevronRight className={`size-4 text-muted transition-transform ${open ? "rotate-90" : ""} ${learner.paths.length ? "" : "invisible"}`} aria-hidden />
                                {learner.name}
                              </button>
                            </td>
                            <td className="px-4 py-2 text-ink-2">
                              {learner.paths.length ? learner.paths.map((p) => `${p.role_label} · ${p.project_name}`).join(", ") : "—"}
                            </td>
                            <td className="px-4 py-2">
                              {learner.steps_total ? (
                                <div className="flex items-center gap-2">
                                  <Meter value={ratio} label={`${learner.name} steps completed`} />
                                  <span className="shrink-0 text-xs text-muted tabular-nums">{learner.steps_completed}/{learner.steps_total}</span>
                                </div>
                              ) : "—"}
                            </td>
                            <td className="px-4 py-2 text-right tabular-nums">{pct(learner.avg_quiz_score)}</td>
                            <td className="px-4 py-2 text-right tabular-nums">{learner.questions_asked}</td>
                            <td className="px-4 py-2 text-ink-2">{formatDate(learner.last_activity)}</td>
                          </tr>
                          {open && (
                            <tr className="border-b border-line bg-surface-2">
                              <td colSpan={6} className="px-6 py-3">
                                <div className="grid gap-3 md:grid-cols-2">
                                  {learner.paths.map((p) => (
                                    <div key={p.path_id} className="rounded-lg border border-line bg-surface p-3">
                                      <div className="text-sm font-medium">{p.role_label} path · {p.project_name}</div>
                                      <div className="mt-1 text-xs text-muted">
                                        {p.steps_completed}/{p.steps_total} steps · {p.quiz_attempts} quiz attempts · average {pct(p.avg_quiz_score)}
                                      </div>
                                      {p.scores.length > 0 && (
                                        <ul className="mt-2 space-y-0.5 text-xs">
                                          {p.scores.map((s, i) => (
                                            <li key={i} className="flex justify-between gap-2">
                                              <span>Step {s.step_index + 1} quiz</span>
                                              <span className="tabular-nums">{s.score}/{s.total} · {formatDate(s.at)}</span>
                                            </li>
                                          ))}
                                        </ul>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          <Card title={<span className="flex items-center gap-2"><MessageCircleQuestion className="size-4" aria-hidden />Most asked questions</span>}>
            {data.top_questions.length === 0 ? (
              <p className="text-sm text-muted">No learner questions yet. Questions asked in <Link className="underline" to="/chat">Chat</Link> by a selected learner appear here.</p>
            ) : (
              <ol className="space-y-2.5">
                {data.top_questions.map((q) => (
                  <li key={q.question} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1">
                    <span className="text-sm text-ink">{q.question}</span>
                    <span className="text-xs text-muted tabular-nums">asked {q.count}×</span>
                    <span className="h-1.5 overflow-hidden">
                      <span className="block h-full rounded-r-[4px]" style={{ width: `${(q.count / maxCount) * 100}%`, background: "var(--series-1)" }} />
                    </span>
                    <span className="text-xs text-muted">{q.learners.join(", ")}</span>
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
