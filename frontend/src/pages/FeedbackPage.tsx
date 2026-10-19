import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FlaskConical, ThumbsDown, ThumbsUp, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { FeedbackItem, Project } from "../api/types";
import { StackedColumns } from "../components/charts";
import { Markdown } from "../components/Markdown";
import { Badge, Button, Card, EmptyState, ErrorBox, Modal, PageHeader, Spinner, StatTile, cx, formatDate, pct } from "../components/ui";

function AddToEvalModal({ project, item, onClose }: { project: Project; item: FeedbackItem; onClose: () => void }) {
  const client = useQueryClient();
  const files = useQuery({ queryKey: ["files", project.id], queryFn: () => api.files(project.id) });
  const [filter, setFilter] = useState("");
  const [file, setFile] = useState(item.eval_question?.expected_file ?? "");
  const [symbol, setSymbol] = useState(item.eval_question?.expected_symbol ?? "");
  const symbols = useQuery({
    queryKey: ["symbols", project.id, file],
    queryFn: () => api.symbols(project.id, file),
    enabled: !!file,
  });
  const save = useMutation({
    mutationFn: () => api.addToEval(item.id, file, symbol || undefined),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["feedback", project.id] });
      client.invalidateQueries({ queryKey: ["eval-questions", project.id] });
      onClose();
    },
  });
  const matches = (files.data ?? []).filter((f) => f.path.toLowerCase().includes(filter.toLowerCase())).slice(0, 200);
  return (
    <Modal
      open
      onClose={onClose}
      title="Add to the evaluation set"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={save.isPending} disabled={!file} onClick={() => save.mutate()}>Save question</Button>
        </>
      }
    >
      <div className="space-y-3 text-sm">
        <div className="rounded-lg bg-surface-2 p-3">
          <div className="text-xs text-muted">Question</div>
          <div className="text-ink">{item.question}</div>
        </div>
        <label className="block">
          <span className="mb-1 block font-medium text-ink-2">Which file actually answers it?</span>
          <input className="h-9 w-full rounded-md border border-line-strong bg-surface px-3 font-mono text-sm"
            placeholder="Filter files…" value={filter} onChange={(event) => setFilter(event.target.value)} />
        </label>
        {files.isLoading && <Spinner />}
        <ul className="max-h-48 overflow-y-auto rounded-md border border-line">
          {matches.map((f) => (
            <li key={f.path}>
              <button
                className={cx("block w-full truncate px-3 py-1.5 text-left font-mono text-xs", f.path === file ? "bg-accent-soft text-accent-ink" : "hover:bg-surface-2")}
                onClick={() => { setFile(f.path); setSymbol(""); }}
              >
                {f.path}
              </button>
            </li>
          ))}
        </ul>
        {file && (
          <label className="block">
            <span className="mb-1 block font-medium text-ink-2">Symbol (optional, makes the check stricter)</span>
            <select className="h-9 w-full rounded-md border border-line-strong bg-surface px-2 font-mono text-xs" value={symbol}
              onChange={(event) => setSymbol(event.target.value)}>
              <option value="">Whole file</option>
              {(symbols.data ?? []).map((s) => (
                <option key={`${s.qualified_name}:${s.start_line}`} value={s.qualified_name}>
                  {s.kind} {s.qualified_name} (line {s.start_line})
                </option>
              ))}
            </select>
          </label>
        )}
        {save.error && <ErrorBox error={save.error} />}
      </div>
    </Modal>
  );
}

function FeedbackRow({ item, onAddToEval }: { item: FeedbackItem; onAddToEval: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-xl border border-line bg-surface p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {item.rating === 1 ? (
              <Badge tone="good"><ThumbsUp className="size-3" aria-hidden /> Helpful</Badge>
            ) : (
              <Badge tone="bad"><ThumbsDown className="size-3" aria-hidden /> Not helpful</Badge>
            )}
            <span className="text-xs text-muted">{formatDate(item.created_at)}{item.learner ? ` · ${item.learner}` : ""}</span>
            {item.eval_question && (
              <Badge tone="accent">
                <FlaskConical className="size-3" aria-hidden /> In eval set: {item.eval_question.expected_file}
                {item.eval_question.expected_symbol ? ` · ${item.eval_question.expected_symbol}` : ""}
              </Badge>
            )}
          </div>
          <div className="mt-1.5 text-sm font-medium text-ink">{item.question}</div>
          {item.comment && <div className="mt-1 text-sm text-ink-2">“{item.comment}”</div>}
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            {open ? "Hide answer" : "Show answer"}
          </Button>
          {item.rating === -1 && (
            <Button size="sm" icon={<FlaskConical className="size-3.5" />} onClick={onAddToEval}>
              {item.eval_question ? "Edit eval entry" : "Add to evaluation set"}
            </Button>
          )}
        </div>
      </div>
      {open && (
        <div className="mt-3 rounded-lg bg-surface-2 p-3">
          <Markdown text={item.answer} />
          <div className="mt-2 text-xs text-muted">
            {item.retrieved_chunk_ids.length} chunks retrieved · {item.cited_chunk_ids.length} cited
          </div>
        </div>
      )}
    </li>
  );
}

export default function FeedbackPage({ project }: { project: Project }) {
  const client = useQueryClient();
  const [filter, setFilter] = useState<"down" | "up" | "all">("down");
  const [editing, setEditing] = useState<FeedbackItem | null>(null);
  const feedback = useQuery({ queryKey: ["feedback", project.id], queryFn: () => api.projectFeedback(project.id) });
  const questions = useQuery({ queryKey: ["eval-questions", project.id], queryFn: () => api.evalQuestions(project.id) });
  const removeQuestion = useMutation({
    mutationFn: (id: number) => api.deleteEvalQuestion(id),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["eval-questions", project.id] });
      client.invalidateQueries({ queryKey: ["feedback", project.id] });
    },
  });
  const stats = feedback.data?.stats;
  const items = useMemo(
    () => (feedback.data?.items ?? []).filter((i) => filter === "all" || (filter === "down" ? i.rating === -1 : i.rating === 1)),
    [feedback.data, filter],
  );
  // Bucket by the viewer's local date (matching the timestamps shown in the list) and fill quiet days with zeros.
  const days = useMemo(() => {
    const localKey = (d: Date) =>
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const buckets = new Map<string, { up: number; down: number }>();
    for (const item of feedback.data?.items ?? []) {
      const key = localKey(new Date(item.created_at));
      const bucket = buckets.get(key) ?? { up: 0, down: 0 };
      if (item.rating === 1) bucket.up += 1;
      else bucket.down += 1;
      buckets.set(key, bucket);
    }
    const keys = [...buckets.keys()].sort();
    if (keys.length > 1) {
      const cursor = new Date(`${keys[0]}T12:00:00`);
      const last = new Date(`${keys[keys.length - 1]}T12:00:00`);
      let guard = 0;
      while (cursor < last && guard++ < 120) {
        cursor.setDate(cursor.getDate() + 1);
        const key = localKey(cursor);
        if (!buckets.has(key)) buckets.set(key, { up: 0, down: 0 });
      }
    }
    return [...buckets.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, values]) => ({
        key,
        label: new Date(`${key}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }),
        values,
      }));
  }, [feedback.data]);

  return (
    <div className="space-y-5">
      <PageHeader
        title={`Feedback · ${project.name}`}
        description="Ratings on answers. Turn thumbs-down questions into evaluation cases and export training pairs."
        actions={
          <a href={api.exportUrl(project.id)} download
            className="inline-flex h-9 items-center gap-1.5 rounded-md border border-line-strong bg-surface px-3.5 text-sm font-medium text-ink hover:bg-surface-2">
            <Download className="size-4" aria-hidden /> Export JSONL training pairs
          </a>
        }
      />
      {feedback.isLoading && <Spinner label="Loading feedback…" />}
      {feedback.error && <ErrorBox error={feedback.error} onRetry={() => feedback.refetch()} />}
      {stats && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Ratings" value={stats.total} sub={`on ${stats.answers} answer${stats.answers === 1 ? "" : "s"}`} />
            <StatTile label="Rated helpful" value={pct(stats.satisfaction)} />
            <StatTile label="Thumbs up" value={stats.up} />
            <StatTile label="Thumbs down" value={stats.down} />
          </div>
          <Card title="Ratings over time">
            {days.length === 0 ? (
              <p className="text-sm text-muted">No ratings yet. Rate answers on the Chat page with the thumbs buttons.</p>
            ) : (
              <StackedColumns
                data={days}
                caption="Helpful and not helpful ratings per day"
                series={[
                  { key: "up", label: "Helpful", color: "var(--status-good)", icon: <ThumbsUp className="size-3" aria-hidden /> },
                  { key: "down", label: "Not helpful", color: "var(--status-critical)", icon: <ThumbsDown className="size-3" aria-hidden /> },
                ]}
              />
            )}
          </Card>
        </>
      )}
      {feedback.data && (
        <Card
          title="Rated answers"
          actions={
            <div className="flex gap-1" role="tablist" aria-label="Filter ratings">
              {(["down", "up", "all"] as const).map((value) => (
                <button key={value} role="tab" aria-selected={filter === value} onClick={() => setFilter(value)}
                  className={cx("rounded-md px-2.5 py-1 text-xs", filter === value ? "bg-accent-soft font-medium text-accent-ink" : "text-ink-2 hover:bg-surface-2")}>
                  {value === "down" ? "Thumbs down" : value === "up" ? "Thumbs up" : "All"}
                </button>
              ))}
            </div>
          }
        >
          {items.length === 0 ? (
            <EmptyState title={filter === "down" ? "No thumbs-down answers" : "Nothing here yet"} />
          ) : (
            <ul className="space-y-2">
              {items.map((item) => (
                <FeedbackRow key={item.id} item={item} onAddToEval={() => setEditing(item)} />
              ))}
            </ul>
          )}
        </Card>
      )}
      <Card title={`Project evaluation set (${questions.data?.length ?? 0} questions)`}>
        {questions.data && questions.data.length === 0 && (
          <p className="text-sm text-muted">
            Questions you add from thumbs-down feedback appear here and can be evaluated on the Evaluation page.
          </p>
        )}
        <ul className="divide-y divide-line">
          {(questions.data ?? []).map((q) => (
            <li key={q.id} className="flex items-start justify-between gap-3 py-2">
              <div className="min-w-0">
                <div className="text-sm text-ink">{q.question}</div>
                <div className="font-mono text-xs text-muted">{q.expected_file}{q.expected_symbol ? ` · ${q.expected_symbol}` : ""}</div>
              </div>
              <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} aria-label="Remove question"
                busy={removeQuestion.isPending && removeQuestion.variables === q.id} onClick={() => removeQuestion.mutate(q.id)} />
            </li>
          ))}
        </ul>
      </Card>
      {editing && <AddToEvalModal project={project} item={editing} onClose={() => setEditing(null)} />}
    </div>
  );
}
