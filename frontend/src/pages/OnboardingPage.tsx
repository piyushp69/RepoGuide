import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpenCheck, CheckCircle2, Circle, FileCode2, GraduationCap, RefreshCw, XCircle } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { LearningPath, PathStep, Project, Quiz, QuizResult } from "../api/types";
import { useOpenCode } from "../components/CodeViewer";
import { JobProgress } from "../components/JobProgress";
import { InlineText } from "../components/Markdown";
import { Badge, Button, Card, EmptyState, ErrorBox, Meter, Modal, PageHeader, Spinner, cx, pct } from "../components/ui";
import { isActive, useJob } from "../hooks/queries";
import { useAppState } from "../state/AppState";

function QuizPanel({ path, stepIndex, onPassed }: { path: LearningPath; stepIndex: number; onPassed: () => void }) {
  const { learner } = useAppState();
  const client = useQueryClient();
  const openCode = useOpenCode();
  const key = ["quiz", path.id, stepIndex];
  const query = useQuery({
    queryKey: key,
    queryFn: () => api.quiz(path.id, stepIndex),
    refetchInterval: (q) => (isActive(q.state.data?.job) ? 1500 : false),
  });
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId, () => client.invalidateQueries({ queryKey: key }));
  const generate = useMutation({
    mutationFn: () => api.generateQuiz(path.id, stepIndex),
    onSuccess: (created) => setJobId(created.id),
  });
  const [answers, setAnswers] = useState<Record<number, number>>({});
  const [result, setResult] = useState<QuizResult | null>(null);
  const submit = useMutation({
    mutationFn: (quiz: Quiz) => api.submitQuiz(quiz.id, learner as string, quiz.questions.map((_, i) => answers[i] ?? null)),
    onSuccess: (outcome) => {
      setResult(outcome);
      if (outcome.passed) onPassed();
    },
  });

  const quiz = query.data?.quiz ?? null;
  const runningJob = isActive(job) ? job : isActive(query.data?.job) ? query.data?.job : null;
  if (query.isLoading) return <Spinner label="Loading quiz…" />;
  if (runningJob) return <JobProgress job={runningJob} />;
  if (!quiz) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" icon={<GraduationCap className="size-3.5" />} busy={generate.isPending} onClick={() => generate.mutate()}>
          Generate quiz
        </Button>
        <span className="text-xs text-muted">3 questions written from this step's code (about 20-40 s).</span>
        {(generate.error || job?.status === "error") && <ErrorBox error={generate.error ?? job?.error} />}
      </div>
    );
  }
  return (
    <div className="space-y-4">
      {quiz.questions.map((question, qIndex) => {
        const outcome = result?.results[qIndex];
        return (
          <fieldset key={question.id} className="space-y-1.5">
            <legend className="text-sm font-medium text-ink">{qIndex + 1}. <InlineText text={question.question} /></legend>
            {question.options.map((option, oIndex) => {
              const chosen = answers[qIndex] === oIndex;
              const isCorrect = outcome && outcome.answer_index === oIndex;
              const isWrongChoice = outcome && chosen && !outcome.correct;
              return (
                <label
                  key={oIndex}
                  className={cx(
                    "flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm",
                    isCorrect ? "border-transparent bg-good-soft" : isWrongChoice ? "border-transparent bg-bad-soft" : chosen ? "border-accent bg-accent-soft" : "border-line hover:bg-surface-2",
                  )}
                >
                  <input
                    type="radio"
                    className="mt-0.5"
                    name={`q-${quiz.id}-${qIndex}`}
                    checked={chosen}
                    disabled={!!result}
                    onChange={() => setAnswers((a) => ({ ...a, [qIndex]: oIndex }))}
                  />
                  <span className="flex-1"><InlineText text={option} /></span>
                  {isCorrect && <CheckCircle2 className="size-4 text-good-ink" aria-label="Correct answer" />}
                  {isWrongChoice && <XCircle className="size-4 text-bad-ink" aria-label="Your answer (incorrect)" />}
                </label>
              );
            })}
            {outcome && (
              <div className="rounded-md bg-surface-2 p-2 text-xs text-ink-2">
                <InlineText text={outcome.explanation} />{" "}
                <button
                  className="inline-flex items-center gap-1 font-mono text-accent-ink underline"
                  onClick={() => openCode({ path: outcome.file, start: outcome.line ?? undefined, end: outcome.line ?? undefined })}
                >
                  <FileCode2 className="size-3" aria-hidden />
                  {outcome.file}{outcome.line ? `:${outcome.line}` : ""}
                </button>
              </div>
            )}
          </fieldset>
        );
      })}
      {result ? (
        <div className="flex flex-wrap items-center gap-3">
          <Badge tone={result.passed ? "good" : "warn"}>
            Score {result.score}/{result.total} {result.passed ? "· step completed" : "· 60% needed to pass"}
          </Badge>
          <Button size="sm" variant="ghost" onClick={() => { setResult(null); setAnswers({}); }}>Try again</Button>
          <Button size="sm" variant="ghost" icon={<RefreshCw className="size-3.5" />} onClick={() => { setResult(null); setAnswers({}); generate.mutate(); }}>
            New questions
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            size="sm"
            variant="primary"
            busy={submit.isPending}
            disabled={!learner || Object.keys(answers).length < quiz.questions.length}
            onClick={() => submit.mutate(quiz)}
          >
            Submit answers
          </Button>
          {!learner && <span className="text-xs text-warn-ink">Choose a learner (top bar) to record your score.</span>}
        </div>
      )}
      {submit.error && <ErrorBox error={submit.error} />}
    </div>
  );
}

function StepCard({ path, step, index, completed, best, onToggle, onPassed }: {
  path: LearningPath;
  step: PathStep;
  index: number;
  completed: boolean;
  best: number | null;
  onToggle: (done: boolean) => void;
  onPassed: () => void;
}) {
  const { learner } = useAppState();
  const openCode = useOpenCode();
  const [quizOpen, setQuizOpen] = useState(false);
  return (
    <li className="rounded-xl border border-line bg-surface p-4">
      <div className="flex items-start gap-3">
        <button
          className={cx("mt-0.5 shrink-0", completed ? "text-good-ink" : "text-muted hover:text-ink")}
          onClick={() => onToggle(!completed)}
          disabled={!learner}
          aria-label={completed ? "Mark step as not done" : "Mark step as done"}
          title={learner ? undefined : "Choose a learner to track progress"}
        >
          {completed ? <CheckCircle2 className="size-5" /> : <Circle className="size-5" />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold text-ink">Step {index + 1}: {step.title}</h3>
            {best !== null && <Badge tone={best >= 0.6 ? "good" : "warn"}>Best quiz score {pct(best)}</Badge>}
          </div>
          {step.goal && <p className="mt-0.5 text-sm text-ink-2">{step.goal}</p>}
          <ul className="mt-3 space-y-2">
            {step.files.map((file) => (
              <li key={file.path} className="rounded-lg bg-surface-2 p-2.5">
                <button
                  className="inline-flex items-center gap-1.5 font-mono text-sm text-accent-ink hover:underline"
                  onClick={() => openCode({ path: file.path })}
                >
                  <FileCode2 className="size-4" aria-hidden />
                  {file.path}
                </button>
                <span className="ml-2 text-xs text-muted">{file.language} · {file.lines} lines</span>
                {file.why && <p className="mt-1 text-sm text-ink-2">{file.why}</p>}
                {file.symbols.length > 0 && (
                  <p className="mt-1 truncate font-mono text-xs text-muted">{file.symbols.join(" · ")}</p>
                )}
              </li>
            ))}
          </ul>
          <div className="mt-3">
            <button className="text-sm font-medium text-accent-ink underline" onClick={() => setQuizOpen((o) => !o)} aria-expanded={quizOpen}>
              {quizOpen ? "Hide quiz" : "Check your understanding"}
            </button>
            {quizOpen && <div className="mt-3"><QuizPanel path={path} stepIndex={index} onPassed={onPassed} /></div>}
          </div>
        </div>
      </div>
    </li>
  );
}

export default function OnboardingPage({ project }: { project: Project }) {
  const { learner } = useAppState();
  const client = useQueryClient();
  const roles = useQuery({ queryKey: ["roles"], queryFn: api.roles });
  const [role, setRole] = useState("backend");
  const key = ["path", project.id, role];
  const query = useQuery({
    queryKey: key,
    queryFn: () => api.path(project.id, role),
    refetchInterval: (q) => (isActive(q.state.data?.job) ? 1500 : false),
  });
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId, () => client.invalidateQueries({ queryKey: key }));
  const generate = useMutation({
    mutationFn: () => api.generatePath(project.id, role),
    onSuccess: (created) => {
      setConfirm(false);
      setJobId(created.id);
    },
  });
  const [confirm, setConfirm] = useState(false);
  const path = query.data?.path ?? null;
  const progressKey = ["progress", path?.id, learner];
  const progress = useQuery({
    queryKey: progressKey,
    queryFn: () => api.progress(path!.id, learner!),
    enabled: !!path && !!learner,
  });
  const toggle = useMutation({
    mutationFn: ({ step, done }: { step: number; done: boolean }) => api.setProgress(path!.id, learner!, step, done),
    onSuccess: (data) => client.setQueryData(progressKey, data),
  });
  const runningJob = isActive(job) ? job : isActive(query.data?.job) ? query.data?.job : null;
  const failedJob = !runningJob && (job?.status === "error" ? job : query.data?.job?.status === "error" && !path ? query.data.job : null);
  const completed = new Set(progress.data?.completed_steps ?? []);
  const done = path ? path.steps.filter((_, i) => completed.has(i)).length : 0;

  return (
    <div>
      <PageHeader
        title={`Onboarding · ${project.name}`}
        description="A role-based reading path through the codebase, with quizzes grounded in the actual code."
        actions={path && (
          <Button icon={<RefreshCw className="size-4" />} onClick={() => setConfirm(true)} disabled={!!runningJob}>
            Regenerate path
          </Button>
        )}
      />
      <div className="mb-5 flex flex-wrap gap-2" role="tablist" aria-label="Role">
        {(roles.data ?? []).map((r) => (
          <button
            key={r.id}
            role="tab"
            aria-selected={role === r.id}
            onClick={() => setRole(r.id)}
            className={cx(
              "rounded-full border px-3.5 py-1.5 text-sm",
              role === r.id ? "border-accent bg-accent-soft font-medium text-accent-ink" : "border-line-strong text-ink-2 hover:bg-surface-2",
            )}
            title={r.focus}
          >
            {r.label}
          </button>
        ))}
      </div>
      {!learner && (
        <div className="mb-4 rounded-lg bg-warn-soft p-3 text-sm text-warn-ink">
          Choose a learner in the top bar to save progress and quiz scores.
        </div>
      )}
      {query.isLoading && <Spinner label="Loading learning path…" />}
      {query.error && <ErrorBox error={query.error} onRetry={() => query.refetch()} />}
      {runningJob && <Card className="mb-4"><JobProgress job={runningJob} /></Card>}
      {failedJob && <div className="mb-4"><ErrorBox error={`Generating the path failed: ${failedJob.error}`} /></div>}
      {generate.error && <div className="mb-4"><ErrorBox error={generate.error} /></div>}
      {query.data && !path && !runningJob && (
        <EmptyState
          icon={<BookOpenCheck className="size-8" />}
          title={`No ${roles.data?.find((r) => r.id === role)?.label ?? role} path yet`}
          action={<Button variant="primary" busy={generate.isPending} disabled={!project.chunk_count} onClick={() => generate.mutate()}>Generate learning path</Button>}
        >
          RepoGuide ranks files for this role using the code graph, then the local model orders them into steps and
          explains why each file matters. Takes under a minute.
        </EmptyState>
      )}
      {path && (
        <div className="space-y-4">
          <Card>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-lg font-semibold text-ink">{path.title}</h2>
                <p className="mt-1 text-sm text-ink-2">{path.overview}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {path.role_fit === "limited" && <Badge tone="warn">Few files are specific to this role in this project</Badge>}
                  {path.stale && <Badge tone="warn">Code changed since this path was generated</Badge>}
                </div>
              </div>
              {learner && (
                <div className="w-48">
                  <div className="mb-1 flex justify-between text-xs text-muted">
                    <span>{learner}'s progress</span>
                    <span className="tabular-nums">{done}/{path.steps.length}</span>
                  </div>
                  <Meter value={path.steps.length ? done / path.steps.length : 0} label="Steps completed" />
                </div>
              )}
            </div>
          </Card>
          <ol className="space-y-3">
            {path.steps.map((step, index) => (
              <StepCard
                key={`${path.id}-${index}`}
                path={path}
                step={step}
                index={index}
                completed={completed.has(index)}
                best={progress.data?.quiz_scores[String(index)]?.best ?? null}
                onToggle={(value) => toggle.mutate({ step: index, done: value })}
                onPassed={() => client.invalidateQueries({ queryKey: progressKey })}
              />
            ))}
          </ol>
          {toggle.error && <ErrorBox error={toggle.error} />}
        </div>
      )}
      <Modal
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Regenerate the learning path?"
        footer={
          <>
            <Button onClick={() => setConfirm(false)}>Cancel</Button>
            <Button variant="primary" busy={generate.isPending} onClick={() => generate.mutate()}>Regenerate</Button>
          </>
        }
      >
        <p className="text-sm text-ink-2">
          A new path replaces the current one for everyone. Progress on the old path stays visible on the manager
          dashboard but does not carry over to the new steps.
        </p>
      </Modal>
    </div>
  );
}
