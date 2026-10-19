import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronRight, Gauge, Play, XCircle } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { api } from "../api/client";
import type { EvalReport, EvalRunSummary } from "../api/types";
import { LineChart } from "../components/charts";
import { JobProgress } from "../components/JobProgress";
import { Markdown } from "../components/Markdown";
import { Badge, Button, Card, EmptyState, ErrorBox, Meter, PageHeader, Spinner, StatTile, cx, formatDate, pct } from "../components/ui";
import { isActive, useCurrentProject, useJob } from "../hooks/queries";

const fmt = (v: number) => `${Math.round(v * 100)}%`;

function delta(current: number | null | undefined, previous: number | null | undefined) {
  if (current === null || current === undefined || previous === null || previous === undefined) return null;
  const diff = Math.round((current - previous) * 1000) / 10;
  if (diff === 0) return { text: "±0 pts vs previous", good: true };
  return { text: `${diff > 0 ? "+" : ""}${diff} pts vs previous`, good: diff > 0 };
}

function RunPanel({ onStarted }: { onStarted: (jobId: number) => void }) {
  const { project } = useCurrentProject();
  const [source, setSource] = useState<"sample" | "project">("sample");
  const [withLlm, setWithLlm] = useState(true);
  const questions = useQuery({
    queryKey: ["eval-questions", project?.id],
    queryFn: () => api.evalQuestions(project!.id),
    enabled: !!project,
  });
  const run = useMutation({
    mutationFn: () => api.runEval({ source, project_id: source === "project" ? project?.id : null, with_llm: withLlm }),
    onSuccess: (job) => onStarted(job.id),
  });
  const custom = questions.data?.length ?? 0;
  return (
    <Card title="Run an evaluation">
      <div className="flex flex-wrap items-end gap-4">
        <fieldset className="space-y-1.5 text-sm">
          <legend className="mb-1 font-medium text-ink-2">Question set</legend>
          <label className="flex items-center gap-2">
            <input type="radio" checked={source === "sample"} onChange={() => setSource("sample")} />
            Bundled sample repo (eval/eval_set.json)
          </label>
          <label className={cx("flex items-center gap-2", !custom && "text-muted")}>
            <input type="radio" checked={source === "project"} disabled={!custom} onChange={() => setSource("project")} />
            {project ? `${project.name}: ${custom} questions from feedback` : "Current project's feedback set"}
          </label>
        </fieldset>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={withLlm} onChange={(event) => setWithLlm(event.target.checked)} />
          Generate answers (measures citation validity; a few minutes)
        </label>
        <Button variant="primary" icon={<Play className="size-4" />} busy={run.isPending} onClick={() => run.mutate()}>
          Run evaluation
        </Button>
      </div>
      {run.error && <div className="mt-3"><ErrorBox error={run.error} /></div>}
    </Card>
  );
}

function QuestionTable({ report }: { report: EvalReport }) {
  const [missesOnly, setMissesOnly] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const rows = report.questions.filter((q) => !missesOnly || !q.hit_at_5);
  return (
    <Card
      title={`Per-question results (${report.questions.length})`}
      actions={
        <label className="flex items-center gap-2 text-xs text-ink-2">
          <input type="checkbox" checked={missesOnly} onChange={(event) => setMissesOnly(event.target.checked)} />
          Misses only
        </label>
      }
      padded={false}
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[46rem] text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              <th className="px-4 py-2 font-medium">Question</th>
              <th className="px-4 py-2 font-medium">Expected</th>
              <th className="px-4 py-2 text-right font-medium">Rank</th>
              <th className="px-4 py-2 font-medium">Hit@5</th>
              <th className="px-4 py-2 font-medium">Citations valid</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((q) => {
              const expanded = open === q.id;
              const valid = q.citations ? q.citations.filter((c) => c.valid).length : null;
              return (
                <Fragment key={q.id}>
                  <tr className="border-b border-line hover:bg-surface-2">
                    <td className="px-4 py-2">
                      <button className="flex items-start gap-1 text-left" onClick={() => setOpen(expanded ? null : q.id)} aria-expanded={expanded}>
                        <ChevronRight className={`mt-0.5 size-4 shrink-0 text-muted transition-transform ${expanded ? "rotate-90" : ""}`} aria-hidden />
                        <span><span className="text-xs text-muted">{q.id}</span> {q.question}</span>
                      </button>
                    </td>
                    <td className="px-4 py-2 font-mono text-xs text-ink-2">
                      {q.expected_file}{q.expected_symbol ? <><br />{q.expected_symbol}</> : null}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums">{q.rank ?? "—"}</td>
                    <td className="px-4 py-2">
                      {q.hit_at_5 ? (
                        <span className="inline-flex items-center gap-1 text-good-ink"><CheckCircle2 className="size-4" aria-hidden /> Hit</span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-bad-ink"><XCircle className="size-4" aria-hidden /> Miss</span>
                      )}
                    </td>
                    <td className="px-4 py-2 tabular-nums">{valid === null ? "—" : `${valid}/${q.citations!.length}`}</td>
                  </tr>
                  {expanded && (
                    <tr className="border-b border-line bg-surface-2">
                      <td colSpan={5} className="space-y-3 px-6 py-3">
                        <div>
                          <div className="mb-1 text-xs font-semibold text-muted">Top 5 retrieved</div>
                          <ol className="list-decimal space-y-0.5 pl-5 font-mono text-xs">
                            {q.top5.map((t, i) => <li key={i}>{t.file_path}:{t.lines} {t.symbol ? `· ${t.symbol}` : ""}</li>)}
                          </ol>
                          <div className="mt-1 text-xs text-muted">
                            Ranks by retriever: vector {q.ablation.vector?.rank ?? "—"}, BM25 {q.ablation.bm25?.rank ?? "—"}, hybrid {q.ablation.hybrid?.rank ?? "—"}
                          </div>
                        </div>
                        {q.answer && (
                          <div>
                            <div className="mb-1 text-xs font-semibold text-muted">Generated answer ({q.seconds}s)</div>
                            <div className="rounded-lg border border-line bg-surface p-3"><Markdown text={q.answer} /></div>
                          </div>
                        )}
                        {q.citations && q.citations.length > 0 && (
                          <ul className="space-y-0.5 text-xs">
                            {q.citations.map((c, i) => (
                              <li key={i} className={c.valid ? "text-good-ink" : "text-bad-ink"}>
                                {c.valid ? "✓" : "✗"} {c.kind === "marker" ? `[${c.n}]` : "mention"}{" "}
                                <span className="font-mono">{c.file_path ?? "?"}{c.start_line ? `:${c.start_line}-${c.end_line}` : ""}</span> — {c.reason}
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function LatestRun({ report, previous, isLatest }: { report: EvalReport; previous: EvalRunSummary | null; isLatest: boolean }) {
  const m = report.metrics;
  const p = previous?.metrics;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2 text-sm text-ink-2">
        <span className="font-medium text-ink">{isLatest ? "Latest run" : "Selected run"}</span>
        <Badge>{report.eval_set.name}</Badge>
        <span>{formatDate(report.created_at)}</span>
        <span>· {report.eval_set.questions} questions{report.eval_set.unanswerable ? ` + ${report.eval_set.unanswerable} unanswerable` : ""}</span>
        <span>· {report.config.chat_model} / {report.config.embed_model}</span>
        <span>· {report.duration_seconds}s</span>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Hit rate @5 (expected symbol)" value={pct(m.hit_at_5, 1)} delta={delta(m.hit_at_5, p?.hit_at_5)}
          sub={`file-level ${pct(m.file_hit_at_5, 1)}`} />
        <StatTile label="Mean reciprocal rank @10" value={m.mrr?.toFixed(3) ?? "—"} delta={delta(m.mrr, p?.mrr)}
          sub={`file-level ${m.file_mrr?.toFixed(3) ?? "—"}`} />
        <StatTile label="Citation validity" value={pct(m.citation_validity, 1)} delta={delta(m.citation_validity, p?.citation_validity)}
          sub={m.citations_checked !== undefined ? `${m.citations_checked} citations checked` : "answers not generated"} />
        <StatTile label="Abstains when code lacks the answer" value={pct(m.abstention_rate)}
          sub={m.unanswerable_questions ? `${m.unanswerable_questions} unanswerable questions` : "not measured"} />
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Answer quality">
          <dl className="space-y-3 text-sm">
            {[
              ["Relevant code reached the model (top-k + graph)", m.in_context_rate],
              ["Answers with at least one citation", m.answers_with_citations],
              ["Answers citing the expected file", m.answers_citing_expected_file],
              ["Answers citing the expected symbol", m.answers_citing_expected_symbol],
              ["Drafts rewritten because they cited nothing", m.answers_rewritten],
              ["Answers with a citation number corrected", m.answers_with_corrected_citations],
            ].map(([label, value]) => (
              <div key={label as string}>
                <div className="mb-1 flex justify-between gap-2"><dt className="text-ink-2">{label}</dt><dd className="tabular-nums">{pct(value as number | null | undefined, 1)}</dd></div>
                {typeof value === "number" && <Meter value={value} label={label as string} />}
              </div>
            ))}
            {m.avg_answer_seconds != null && <div className="text-xs text-muted">Average answer time {m.avg_answer_seconds.toFixed(1)}s</div>}
          </dl>
        </Card>
        <Card title="Retriever ablation (hit@5 · MRR)">
          <table className="w-full text-sm">
            <tbody>
              {Object.entries(report.ablation).map(([mode, values]) => (
                <tr key={mode} className="border-b border-line last:border-0">
                  <td className="py-2 pr-3 font-medium capitalize text-ink">{mode === "bm25" ? "BM25 only" : mode === "vector" ? "Vector only" : "Hybrid (used)"}</td>
                  <td className="w-1/3 py-2 pr-3"><Meter value={values.hit_at_5 ?? 0} label={`${mode} hit rate`} /></td>
                  <td className="py-2 pr-3 text-right tabular-nums">{pct(values.hit_at_5, 1)}</td>
                  <td className="py-2 text-right tabular-nums text-muted">{values.mrr?.toFixed(3) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-muted">Hybrid adds BM25 and symbol lookup to vector search; see DECISIONS.md for the trade-off.</p>
        </Card>
      </div>
      {report.unanswerable.length > 0 && (
        <Card title="Unanswerable questions (the model should say it cannot find the answer)">
          <ul className="space-y-3">
            {report.unanswerable.map((u) => (
              <li key={u.id}>
                <div className="flex items-center gap-2 text-sm font-medium">
                  {u.abstained ? <CheckCircle2 className="size-4 text-good-ink" aria-label="Abstained" /> : <XCircle className="size-4 text-bad-ink" aria-label="Answered anyway" />}
                  {u.question}
                </div>
                <p className="ml-6 text-xs text-muted">{u.answer.slice(0, 240)}{u.answer.length > 240 ? "…" : ""}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <QuestionTable report={report} />
      {report.files && <p className="text-xs text-muted">Saved to <span className="font-mono">{report.files.json}</span> and <span className="font-mono">{report.files.markdown}</span>.</p>}
    </div>
  );
}

export default function EvaluationPage() {
  const client = useQueryClient();
  const runs = useQuery({ queryKey: ["eval-runs"], queryFn: api.evalRuns });
  const evalJobs = useQuery({
    queryKey: ["eval-jobs"],
    queryFn: api.evalJobs,
    refetchInterval: (q) => (q.state.data?.some(isActive) ? 2000 : false),
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId, (finished) => {
    client.invalidateQueries({ queryKey: ["eval-runs"] });
    client.invalidateQueries({ queryKey: ["eval-jobs"] });
    const runId = (finished.result as { run_id?: string } | null)?.run_id;
    if (runId) setSelected(runId);
  });
  const activeJob = isActive(job) ? job : evalJobs.data?.find(isActive) ?? null;
  const list = runs.data ?? [];
  const current = selected ?? list[0]?.run_id ?? null;
  const report = useQuery({ queryKey: ["eval-run", current], queryFn: () => api.evalRun(current!), enabled: !!current });
  const currentSummary = list.find((r) => r.run_id === current) ?? null;
  const sameSet = useMemo(
    () => list.filter((r) => r.eval_set.name === currentSummary?.eval_set.name).slice().reverse(),
    [list, currentSummary],
  );
  // Deltas only compare like with like: the closest earlier run of the same set with the same question count.
  const previous = useMemo(() => {
    const index = sameSet.findIndex((r) => r.run_id === current);
    for (let i = index - 1; i >= 0; i--) {
      if (sameSet[i].metrics.questions === sameSet[index].metrics.questions) return sameSet[i];
    }
    return null;
  }, [sameSet, current]);

  return (
    <div className="space-y-5">
      <PageHeader title="Evaluation" description="How accurate RepoGuide is: retrieval hit rate, ranking quality, citation validity and abstention." />
      <RunPanel onStarted={setJobId} />
      {activeJob && <Card><JobProgress job={activeJob} /></Card>}
      {job?.status === "error" && <ErrorBox error={`Evaluation failed: ${job.error}`} />}
      {runs.isLoading && <Spinner label="Loading results…" />}
      {runs.error && <ErrorBox error={runs.error} onRetry={() => runs.refetch()} />}
      {runs.data && list.length === 0 && !activeJob && (
        <EmptyState icon={<Gauge className="size-8" />} title="No evaluation results yet">
          Run the evaluation above, or from a terminal: <code className="font-mono">python backend/scripts/evaluate.py</code>
        </EmptyState>
      )}
      {sameSet.length > 0 && (
        <Card title={`History · ${currentSummary?.eval_set.name}`}>
          <LineChart
            caption="Hit rate at 5, mean reciprocal rank and citation validity for each evaluation run"
            format={fmt}
            points={sameSet.map((r) => ({
              key: r.run_id,
              label: new Date(r.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
                " " + new Date(r.created_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }),
              values: { hit: r.metrics.hit_at_5, mrr: r.metrics.mrr, cite: r.metrics.citation_validity ?? null },
            }))}
            series={[
              { key: "hit", label: "Hit@5", color: "var(--series-1)" },
              { key: "mrr", label: "MRR", color: "var(--series-2)" },
              { key: "cite", label: "Citation validity", color: "var(--series-3)" },
            ]}
          />
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[36rem] text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-muted">
                  <th className="py-1.5 pr-3 font-medium">Run</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Questions</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Hit@5</th>
                  <th className="py-1.5 pr-3 text-right font-medium">MRR</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Citation validity</th>
                  <th className="py-1.5 font-medium">Answers</th>
                </tr>
              </thead>
              <tbody>
                {[...sameSet].reverse().map((r) => (
                  <tr key={r.run_id} className={cx("border-b border-line last:border-0", r.run_id === current && "bg-accent-soft")}>
                    <td className="py-1.5 pr-3">
                      <button className="text-left text-accent-ink underline" onClick={() => setSelected(r.run_id)}>{formatDate(r.created_at)}</button>
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{r.metrics.questions}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{pct(r.metrics.hit_at_5, 1)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{r.metrics.mrr?.toFixed(3)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{pct(r.metrics.citation_validity, 1)}</td>
                    <td className="py-1.5 text-ink-2">{r.config.with_llm ? "generated" : "retrieval only"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {list.some((r) => r.eval_set.name !== currentSummary?.eval_set.name) && (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted">
              Other sets:
              {[...new Set(list.map((r) => r.eval_set.name))].filter((n) => n !== currentSummary?.eval_set.name).map((name) => (
                <button key={name} className="underline" onClick={() => setSelected(list.find((r) => r.eval_set.name === name)!.run_id)}>{name}</button>
              ))}
            </div>
          )}
        </Card>
      )}
      {report.isLoading && current && <Spinner label="Loading run…" />}
      {report.error && <ErrorBox error={report.error} />}
      {report.data && <LatestRun report={report.data} previous={previous} isLatest={current === list[0]?.run_id} />}
    </div>
  );
}
