import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, Boxes, ChevronRight, Code2, FileCode2, Network, PlayCircle, RefreshCw, Workflow } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { Overview, Project } from "../api/types";
import { useOpenCode } from "../components/CodeViewer";
import { HBars } from "../components/charts";
import { JobProgress } from "../components/JobProgress";
import { MermaidDiagram } from "../components/MermaidDiagram";
import { Badge, Button, Card, EmptyState, ErrorBox, PageHeader, Spinner, formatDate } from "../components/ui";
import { isActive, useInvalidate, useJob } from "../hooks/queries";

function FileLink({ path, line, label }: { path: string; line?: number | null; label?: string }) {
  const openCode = useOpenCode();
  return (
    <button
      className="inline-flex max-w-full items-center gap-1 rounded px-1 font-mono text-xs text-accent-ink hover:bg-accent-soft"
      onClick={() => openCode({ path, start: line ?? undefined, end: line ?? undefined })}
      title={`Open ${path}${line ? `:${line}` : ""}`}
    >
      <FileCode2 className="size-3.5 shrink-0" aria-hidden />
      <span className="truncate">{label ?? path}{line ? `:${line}` : ""}</span>
    </button>
  );
}

function TechStack({ overview }: { overview: Overview }) {
  const groups = new Map<string, Overview["tech_stack"]>();
  for (const item of overview.tech_stack) {
    groups.set(item.category, [...(groups.get(item.category) ?? []), item]);
  }
  if (!groups.size) return <p className="text-sm text-muted">No known frameworks or languages detected.</p>;
  return (
    <dl className="space-y-2.5">
      {[...groups.entries()].map(([category, items]) => (
        <div key={category} className="grid gap-1 sm:grid-cols-[9rem_1fr]">
          <dt className="text-xs font-medium text-muted">{category}</dt>
          <dd className="flex flex-wrap gap-1.5">
            {items.map((item) => (
              <span key={item.name} title={item.evidence.join("\n")}
                className="rounded-md border border-line bg-surface-2 px-2 py-0.5 text-xs text-ink">
                {item.name}
              </span>
            ))}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ModuleMap({ overview }: { overview: Overview }) {
  const openCode = useOpenCode();
  const [open, setOpen] = useState<string | null>(overview.modules[0]?.path ?? null);
  return (
    <ul className="divide-y divide-line">
      {overview.modules.map((module) => {
        const expanded = open === module.path;
        return (
          <li key={module.path} className="py-2">
            <button
              className="flex w-full items-start gap-2 rounded-md px-1 py-1 text-left hover:bg-surface-2"
              onClick={() => setOpen(expanded ? null : module.path)}
              aria-expanded={expanded}
            >
              <ChevronRight className={`mt-0.5 size-4 shrink-0 text-muted transition-transform ${expanded ? "rotate-90" : ""}`} aria-hidden />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-mono text-sm font-medium text-ink">{module.path}/</span>
                  <span className="text-xs text-muted">{module.files.length} files · {module.lines} lines</span>
                </div>
                {module.description && <p className="mt-0.5 text-sm text-ink-2">{module.description}</p>}
              </div>
            </button>
            {expanded && (
              <div className="mt-2 grid gap-4 pl-7 md:grid-cols-2">
                <div>
                  <div className="mb-1 text-xs font-semibold text-muted">Files</div>
                  <ul className="space-y-0.5">
                    {module.files.map((file) => (
                      <li key={file.path} className="flex items-center justify-between gap-2">
                        <FileLink path={file.path} />
                        <span className="shrink-0 text-xs text-muted tabular-nums">{file.lines}</span>
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="space-y-3">
                  {module.key_symbols.length > 0 && (
                    <div>
                      <div className="mb-1 text-xs font-semibold text-muted">Key symbols</div>
                      <ul className="space-y-0.5">
                        {module.key_symbols.map((symbol) => (
                          <li key={`${symbol.file}:${symbol.name}`}>
                            <button className="w-full truncate rounded px-1 text-left text-xs hover:bg-surface-2"
                              onClick={() => openCode({ path: symbol.file, start: symbol.line, end: symbol.line })}
                              title={symbol.doc || undefined}>
                              <span className="text-muted">{symbol.kind}</span>{" "}
                              <span className="font-mono text-ink">{symbol.name}</span>
                              {symbol.doc && <span className="text-muted"> — {symbol.doc}</span>}
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {(module.depends_on.length > 0 || module.used_by.length > 0) && (
                    <div className="space-y-1 text-xs">
                      {module.depends_on.length > 0 && (
                        <div><span className="text-muted">Depends on:</span> <span className="font-mono">{module.depends_on.join(", ")}</span></div>
                      )}
                      {module.used_by.length > 0 && (
                        <div><span className="text-muted">Used by:</span> <span className="font-mono">{module.used_by.join(", ")}</span></div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function OverviewBody({ overview }: { overview: Overview }) {
  const [showSource, setShowSource] = useState(false);
  const languages = overview.languages.slice(0, 6);
  const otherLines = overview.languages.slice(6).reduce((sum, l) => sum + l.lines, 0);
  return (
    <div className="space-y-5">
      {overview.warnings.length > 0 && (
        <div className="rounded-lg bg-warn-soft p-3 text-sm text-warn-ink" role="status">
          {overview.warnings.map((w) => <div key={w}>{w}</div>)}
        </div>
      )}
      <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
        <Card title="What this project is">
          {overview.summary ? (
            <p className="text-sm leading-relaxed text-ink">{overview.summary}</p>
          ) : (
            <p className="text-sm text-muted">The summary could not be generated. Use Regenerate to try again.</p>
          )}
          <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
            <span>{overview.project.files} files</span>
            <span>{overview.project.lines.toLocaleString()} lines</span>
            <span>{overview.project.chunks} chunks</span>
            <span>{overview.graph.internal_imports} internal imports</span>
            <span>{overview.graph.calls} resolved calls</span>
          </div>
        </Card>
        <Card title="Lines of code by language">
          <HBars
            items={[
              ...languages.map((l) => ({ label: l.language, value: l.lines, hint: `${l.files} files` })),
              ...(otherLines ? [{ label: "other", value: otherLines }] : []),
            ]}
            format={(v) => v.toLocaleString()}
          />
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title={<span className="flex items-center gap-2"><Boxes className="size-4" aria-hidden />Tech stack</span>}>
          <TechStack overview={overview} />
        </Card>
        <Card title={<span className="flex items-center gap-2"><PlayCircle className="size-4" aria-hidden />Entry points</span>}>
          {overview.entry_points.length === 0 ? (
            <p className="text-sm text-muted">No entry points detected.</p>
          ) : (
            <ul className="space-y-2">
              {overview.entry_points.map((entry) => (
                <li key={entry.file}>
                  <FileLink path={entry.file} line={entry.line} />
                  <p className="ml-6 text-xs text-muted">{entry.reason}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card
        title={<span className="flex items-center gap-2"><Network className="size-4" aria-hidden />Architecture</span>}
        actions={
          <Button size="sm" variant="ghost" icon={<Code2 className="size-3.5" />} onClick={() => setShowSource((s) => !s)}>
            {showSource ? "Hide source" : "Mermaid source"}
          </Button>
        }
      >
        <p className="mb-3 text-xs text-muted">
          Folders and their import dependencies (dotted arrows: HTTP calls and key frameworks). Green boxes contain entry points.
        </p>
        {overview.mermaid ? (
          <MermaidDiagram code={overview.mermaid} />
        ) : (
          <p className="text-sm text-muted">No folders with indexed source files, so there is no diagram to draw.</p>
        )}
        {showSource && overview.mermaid && <pre className="mt-3 overflow-x-auto rounded-lg bg-sunken p-3 text-xs">{overview.mermaid}</pre>}
      </Card>

      <Card title={<span className="flex items-center gap-2"><Workflow className="size-4" aria-hidden />Main execution flow</span>}>
        {overview.execution_flow.length === 0 ? (
          <p className="text-sm text-muted">No execution flow could be traced (no entry point was found or the model was unavailable).</p>
        ) : (
          <ol className="space-y-3">
            {overview.execution_flow.map((step, index) => (
              <li key={index} className="flex gap-3">
                <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent-ink">
                  {index + 1}
                </span>
                <div className="min-w-0">
                  <div className="text-sm font-medium text-ink">{step.title}</div>
                  <p className="text-sm text-ink-2">{step.description}</p>
                  {step.files.length > 0 && (
                    <div className="mt-1 flex flex-wrap items-center gap-1">
                      {step.files.map((file, i) => (
                        <span key={file} className="flex items-center gap-1">
                          {i > 0 && <ArrowRight className="size-3 text-muted" aria-hidden />}
                          <FileLink path={file} />
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </Card>

      <Card title="What lives where">
        <ModuleMap overview={overview} />
      </Card>
      <p className="text-xs text-muted">
        Generated {formatDate(overview.generated_at)} by {overview.model}. Tech stack, entry points, module map and
        diagram come from static analysis; the summary, module descriptions and flow are written by the local model.
      </p>
    </div>
  );
}

export default function OverviewPage({ project }: { project: Project }) {
  const invalidate = useInvalidate();
  const query = useQuery({
    queryKey: ["overview", project.id],
    queryFn: () => api.overview(project.id),
    refetchInterval: (q) => (isActive(q.state.data?.job) || isActive(q.state.data?.index_job) ? 2000 : false),
  });
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId, () => invalidate(["overview", project.id]));
  const regenerate = useMutation({
    mutationFn: () => api.regenerateOverview(project.id),
    onSuccess: (created) => setJobId(created.id),
  });
  const overview = query.data?.overview ?? null;
  const runningJob = isActive(job) ? job : isActive(query.data?.job) ? query.data?.job : isActive(query.data?.index_job) ? query.data?.index_job : null;

  return (
    <div>
      <PageHeader
        title={`Overview · ${project.name}`}
        description="Purpose, architecture, flow and where each component lives."
        actions={
          <>
            {overview?.stale && <Badge tone="warn">Code changed since this overview</Badge>}
            <Button icon={<RefreshCw className="size-4" />} busy={regenerate.isPending || !!runningJob}
              disabled={!project.chunk_count} onClick={() => regenerate.mutate()}>
              Regenerate
            </Button>
          </>
        }
      />
      {regenerate.error && <div className="mb-4"><ErrorBox error={regenerate.error} /></div>}
      {runningJob && (
        <Card className="mb-5"><JobProgress job={runningJob} /></Card>
      )}
      {query.isLoading && <Spinner label="Loading overview…" />}
      {query.error && <ErrorBox error={query.error} onRetry={() => query.refetch()} />}
      {query.data && !overview && !runningJob && (
        <EmptyState
          title={project.chunk_count ? "No overview yet" : "Project not indexed yet"}
          action={project.chunk_count ? <Button variant="primary" onClick={() => regenerate.mutate()}>Generate overview</Button> : undefined}
        >
          {project.chunk_count
            ? "The overview is generated after indexing. It takes about a minute with a 7B model."
            : "Index the project on the Projects page first."}
        </EmptyState>
      )}
      {overview && <OverviewBody overview={overview} />}
    </div>
  );
}
