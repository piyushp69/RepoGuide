import { useMutation } from "@tanstack/react-query";
import { Check, FolderGit2, GitBranch, HardDrive, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Project } from "../api/types";
import { JobProgress } from "../components/JobProgress";
import { Badge, Button, Card, EmptyState, ErrorBox, Modal, PageHeader, Spinner, formatDate } from "../components/ui";
import { isActive, useInvalidate, useProjects } from "../hooks/queries";
import { useAppState } from "../state/AppState";

const SKIP_LABELS: Record<string, string> = {
  gitignore: ".gitignore",
  ignored_dir: "dependency/build folders",
  virtualenv: "virtual envs",
  binary: "binaries",
  lockfile: "lock files",
  generated: "minified/generated",
  too_large: "over size limit",
  secret: "secrets (.env, keys)",
  data: "data/log dumps",
  empty: "empty",
  symlink: "symlinks",
  over_file_limit: "over file limit",
  unreadable: "unreadable",
};

function statusBadge(project: Project) {
  if (isActive(project.job)) return <Badge tone="accent">Indexing</Badge>;
  if (project.status === "ready") return <Badge tone="good">Ready</Badge>;
  if (project.status === "error") return <Badge tone="bad">Error</Badge>;
  return <Badge>Not indexed</Badge>;
}

function AddProject() {
  const [source, setSource] = useState("");
  const [name, setName] = useState("");
  const invalidate = useInvalidate();
  const { setProjectId } = useAppState();
  const create = useMutation({
    mutationFn: () => api.createProject(source.trim(), name.trim()),
    onSuccess: ({ project }) => {
      setProjectId(project.id);
      setSource("");
      setName("");
      invalidate(["projects"]);
    },
  });
  return (
    <Card title="Add a project">
      <form
        className="grid gap-3 md:grid-cols-[1fr_14rem_auto]"
        onSubmit={(event) => {
          event.preventDefault();
          if (source.trim()) create.mutate();
        }}
      >
        <label className="text-sm">
          <span className="mb-1 block font-medium text-ink-2">Local folder path or GitHub URL</span>
          <input
            className="h-9 w-full rounded-md border border-line-strong bg-surface px-3 font-mono text-sm"
            placeholder="D:\code\my-app   or   https://github.com/owner/repo"
            value={source}
            onChange={(event) => setSource(event.target.value)}
            required
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium text-ink-2">Display name (optional)</span>
          <input
            className="h-9 w-full rounded-md border border-line-strong bg-surface px-3 text-sm"
            value={name}
            maxLength={120}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <div className="flex items-end">
          <Button variant="primary" type="submit" busy={create.isPending} disabled={!source.trim()} className="w-full md:w-auto">
            Add &amp; index
          </Button>
        </div>
      </form>
      <p className="mt-3 text-xs text-muted">
        Code never leaves this machine: GitHub repositories are cloned into the local workspace, embeddings and
        answers come from your local Ollama models. .gitignore is respected; binaries, lock files, build output,
        dependencies, virtual environments, secrets and large files are skipped.
      </p>
      {create.error && <div className="mt-3"><ErrorBox error={create.error} /></div>}
    </Card>
  );
}

function ProjectRow({ project, selected }: { project: Project; selected: boolean }) {
  const { setProjectId } = useAppState();
  const invalidate = useInvalidate();
  const navigate = useNavigate();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const index = useMutation({
    mutationFn: (full: boolean) => api.indexProject(project.id, full),
    onSuccess: () => invalidate(["projects"]),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteProject(project.id),
    onSuccess: () => {
      setConfirmDelete(false);
      invalidate(["projects"]);
    },
  });
  const running = isActive(project.job);
  const skipped = Object.entries(project.skipped ?? {}).filter(([, count]) => count > 0);
  const result = project.job?.status === "done" ? project.job.result : null;
  const warnings = (result?.overview_warnings as string[] | undefined) ?? [];

  return (
    <li className={`rounded-xl border bg-surface p-4 ${selected ? "border-accent" : "border-line"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {project.source_type === "github" ? (
              <GitBranch className="size-4 text-muted" aria-label="GitHub repository" />
            ) : (
              <HardDrive className="size-4 text-muted" aria-hidden />
            )}
            <h3 className="font-semibold text-ink">{project.name}</h3>
            {statusBadge(project)}
            {selected && (
              <Badge tone="accent">
                <Check className="size-3" aria-hidden /> Current
              </Badge>
            )}
          </div>
          <div className="mt-1 truncate font-mono text-xs text-muted" title={project.source}>{project.source}</div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
            <span>{project.file_count} files</span>
            <span>{project.chunk_count} chunks</span>
            <span>Indexed {formatDate(project.last_indexed_at)}</span>
            {project.embed_model && <span>Embeddings: {project.embed_model}</span>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {!selected && (
            <Button size="sm" variant="primary" onClick={() => setProjectId(project.id)}>
              Switch to
            </Button>
          )}
          <Button size="sm" onClick={() => { setProjectId(project.id); navigate("/overview"); }} disabled={!project.chunk_count}>
            Overview
          </Button>
          <Button size="sm" icon={<RefreshCw className="size-3.5" />} busy={index.isPending} disabled={running}
            onClick={() => index.mutate(false)} title="Re-index only files that changed">
            Re-index
          </Button>
          <Button size="sm" variant="ghost" disabled={running} onClick={() => index.mutate(true)} title="Rebuild the whole index">
            Full rebuild
          </Button>
          <Button size="sm" variant="danger" icon={<Trash2 className="size-3.5" />} disabled={running}
            onClick={() => setConfirmDelete(true)} aria-label={`Delete ${project.name}`} />
        </div>
      </div>
      {running && <div className="mt-3"><JobProgress job={project.job} /></div>}
      {project.status === "error" && !running && project.error && (
        <div className="mt-3"><ErrorBox error={project.error} onRetry={() => index.mutate(false)} /></div>
      )}
      {project.job?.status === "error" && !running && project.status !== "error" && (
        <div className="mt-3"><JobProgress job={project.job} /></div>
      )}
      {!running && result && (
        <div className="mt-3 text-xs text-muted">
          Last run: {String(result.added ?? 0)} added, {String(result.changed ?? 0)} changed,{" "}
          {String(result.removed ?? 0)} removed, {String(result.unchanged ?? 0)} unchanged ·{" "}
          {String(result.embedded ?? 0)} chunks embedded · {String(result.edges ?? 0)} graph edges
        </div>
      )}
      {warnings.length > 0 && !running && (
        <div className="mt-2 text-xs text-warn-ink">{warnings.join(" · ")}</div>
      )}
      {skipped.length > 0 && (
        <details className="mt-2 text-xs text-muted">
          <summary className="cursor-pointer">Skipped {skipped.reduce((s, [, c]) => s + c, 0)} files/folders</summary>
          <div className="mt-1 flex flex-wrap gap-2">
            {skipped.map(([reason, count]) => (
              <Badge key={reason}>{SKIP_LABELS[reason] ?? reason}: {count}</Badge>
            ))}
          </div>
        </details>
      )}
      {(index.error || remove.error) && <div className="mt-3"><ErrorBox error={index.error ?? remove.error} /></div>}
      <Modal
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title={`Delete ${project.name}?`}
        footer={
          <>
            <Button onClick={() => setConfirmDelete(false)}>Cancel</Button>
            <Button variant="danger" busy={remove.isPending} onClick={() => remove.mutate()}>Delete project</Button>
          </>
        }
      >
        <p className="text-sm text-ink-2">
          This removes the index, chat history, onboarding paths and feedback for this project.
          {project.source_type === "local"
            ? " Your folder on disk is not touched."
            : " The local clone in RepoGuide's workspace is deleted."}
        </p>
      </Modal>
    </li>
  );
}

export default function ProjectsPage() {
  const projects = useProjects();
  const { projectId } = useAppState();
  return (
    <div className="space-y-6">
      <PageHeader title="Projects" description="Add, index and switch between the codebases RepoGuide knows about." />
      <AddProject />
      {projects.isLoading && <Spinner label="Loading projects…" />}
      {projects.error && <ErrorBox error={projects.error} onRetry={() => projects.refetch()} />}
      {projects.data && projects.data.length === 0 && (
        <EmptyState icon={<FolderGit2 className="size-8" />} title="No projects yet">
          Try the bundled sample app: add the folder <code className="font-mono">eval/sample_repo</code> from this
          repository (use its absolute path).
        </EmptyState>
      )}
      {projects.data && projects.data.length > 0 && (
        <ul className="space-y-3">
          {projects.data.map((project) => (
            <ProjectRow key={project.id} project={project} selected={project.id === projectId} />
          ))}
        </ul>
      )}
    </div>
  );
}
