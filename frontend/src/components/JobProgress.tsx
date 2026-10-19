import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import type { Job } from "../api/types";
import { isActive } from "../hooks/queries";
import { Meter } from "./ui";

const PHASES: Record<string, string> = {
  queued: "Waiting in queue",
  starting: "Starting",
  cloning: "Fetching repository",
  resetting: "Resetting index",
  scanning: "Scanning files",
  parsing: "Parsing & chunking",
  embedding: "Embedding chunks",
  graph: "Building code graph",
  indexed: "Indexed",
  overview: "Generating overview",
  planning: "Planning learning path",
  quiz: "Generating quiz",
  indexing: "Indexing sample repo",
  evaluating: "Evaluating",
  done: "Done",
};

export function JobProgress({ job, compact = false }: { job: Job | null | undefined; compact?: boolean }) {
  if (!job) return null;
  const active = isActive(job);
  const phase = PHASES[job.phase ?? ""] ?? job.phase ?? job.status;
  const fraction = job.total > 0 ? job.current / job.total : null;
  if (job.status === "error") {
    return (
      <div className="flex items-start gap-2 text-sm text-bad-ink" role="alert">
        <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
        <span className="break-words">Failed: {job.error}</span>
      </div>
    );
  }
  if (!active) {
    return compact ? null : (
      <div className="flex items-center gap-2 text-sm text-good-ink">
        <CheckCircle2 className="size-4" aria-hidden /> Finished
      </div>
    );
  }
  return (
    <div className="space-y-1.5" role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-sm text-ink-2">
        <Loader2 className="size-4 animate-spin text-accent-ink" aria-hidden />
        <span className="font-medium">{phase}</span>
        {fraction !== null && (
          <span className="text-muted tabular-nums">
            {job.current}/{job.total}
          </span>
        )}
      </div>
      {fraction !== null && <Meter value={fraction} label={phase} />}
      {!compact && job.message && <div className="truncate text-xs text-muted" title={job.message}>{job.message}</div>}
    </div>
  );
}
