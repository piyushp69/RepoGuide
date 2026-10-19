import type {
  ChatEvent,
  ChatMessage,
  Dashboard,
  EvalQuestion,
  EvalReport,
  EvalRunSummary,
  FeedbackItem,
  FeedbackStats,
  FileSlice,
  Health,
  Job,
  LearningPath,
  Learner,
  Overview,
  Progress,
  Project,
  ProjectFile,
  Quiz,
  QuizResult,
  Related,
  Role,
  SymbolInfo,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const BACKEND_DOWN = "Cannot reach the RepoGuide backend. Is it running?";

async function errorFrom(response: Response): Promise<ApiError> {
  let message = `${response.status} ${response.statusText}`;
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") message = body.detail;
    else if (Array.isArray(body?.detail)) message = body.detail.map((d: { msg?: string }) => d.msg).join("; ");
  } catch {
    // Not JSON, so not from the backend: a gateway error here means the dev proxy could not reach it.
    if ([502, 503, 504].includes(response.status)) message = BACKEND_DOWN;
  }
  return new ApiError(response.status, message);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, BACKEND_DOWN);
  }
  if (!response.ok) throw await errorFrom(response);
  return (await response.json()) as T;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const del = <T>(path: string) => request<T>(path, { method: "DELETE" });
const qs = (params: Record<string, string | number | null | undefined>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
};

export const api = {
  health: () => get<Health>("/api/health"),

  projects: () => get<Project[]>("/api/projects"),
  project: (id: number) => get<Project>(`/api/projects/${id}`),
  createProject: (source: string, name?: string) =>
    post<{ project: Project; job: Job | null }>("/api/projects", { source, name: name || null, index: true }),
  renameProject: (id: number, name: string) =>
    request<Project>(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  deleteProject: (id: number) => del<{ deleted: boolean }>(`/api/projects/${id}`),
  indexProject: (id: number, full = false) => post<Job>(`/api/projects/${id}/index`, { full }),
  job: (id: number) => get<Job>(`/api/jobs/${id}`),

  files: (id: number) => get<ProjectFile[]>(`/api/projects/${id}/files`),
  symbols: (id: number, path?: string) => get<SymbolInfo[]>(`/api/projects/${id}/symbols${qs({ path })}`),
  file: (id: number, path: string, start?: number, end?: number, context = 0) =>
    get<FileSlice>(`/api/projects/${id}/file${qs({ path, start, end, context })}`),
  related: (id: number, chunkId: string) => get<Related>(`/api/projects/${id}/chunks/${chunkId}/related`),

  overview: (id: number) => get<{ overview: Overview | null; job: Job | null; index_job: Job | null }>(
    `/api/projects/${id}/overview`,
  ),
  regenerateOverview: (id: number) => post<Job>(`/api/projects/${id}/overview`),

  chatHistory: (id: number, learner: string | null) =>
    get<ChatMessage[]>(`/api/projects/${id}/chat${qs({ learner })}`),
  clearChat: (id: number, learner: string | null) =>
    del<{ deleted: number }>(`/api/projects/${id}/chat${qs({ learner })}`),
  feedback: (messageId: number, rating: 1 | -1, comment?: string) =>
    post<FeedbackItem>(`/api/messages/${messageId}/feedback`, { rating, comment: comment || null }),
  projectFeedback: (id: number, rating?: number) =>
    get<{ items: FeedbackItem[]; stats: FeedbackStats }>(`/api/projects/${id}/feedback${qs({ rating })}`),
  addToEval: (feedbackId: number, expectedFile: string, expectedSymbol?: string) =>
    post<EvalQuestion>(`/api/feedback/${feedbackId}/eval`, {
      expected_file: expectedFile,
      expected_symbol: expectedSymbol || null,
    }),
  evalQuestions: (id: number) => get<EvalQuestion[]>(`/api/projects/${id}/eval-questions`),
  deleteEvalQuestion: (questionId: number) => del<{ deleted: boolean }>(`/api/eval-questions/${questionId}`),
  exportUrl: (id: number) => `/api/projects/${id}/feedback/export`,

  learners: () => get<Learner[]>("/api/learners"),
  addLearner: (name: string) => post<Learner>("/api/learners", { name }),
  roles: () => get<Role[]>("/api/roles"),
  path: (id: number, role: string) =>
    get<{ path: LearningPath | null; job: Job | null }>(`/api/projects/${id}/onboarding/${role}`),
  generatePath: (id: number, role: string) => post<Job>(`/api/projects/${id}/onboarding/${role}`),
  progress: (pathId: number, learner: string) => get<Progress>(`/api/paths/${pathId}/progress${qs({ learner })}`),
  setProgress: (pathId: number, learner: string, stepIndex: number, completed: boolean) =>
    post<Progress>(`/api/paths/${pathId}/progress`, { learner, step_index: stepIndex, completed }),
  quiz: (pathId: number, step: number) =>
    get<{ quiz: Quiz | null; job: Job | null }>(`/api/paths/${pathId}/steps/${step}/quiz`),
  generateQuiz: (pathId: number, step: number) => post<Job>(`/api/paths/${pathId}/steps/${step}/quiz`),
  submitQuiz: (quizId: number, learner: string, answers: (number | null)[]) =>
    post<QuizResult>(`/api/quizzes/${quizId}/attempts`, { learner, answers }),
  dashboard: (projectId: number | null) => get<Dashboard>(`/api/manager/dashboard${qs({ project_id: projectId })}`),

  evalRuns: () => get<EvalRunSummary[]>("/api/eval/runs"),
  evalRun: (runId: string) => get<EvalReport>(`/api/eval/runs/${encodeURIComponent(runId)}`),
  evalJobs: () => get<Job[]>("/api/eval/jobs"),
  runEval: (body: { source: "sample" | "project"; project_id?: number | null; with_llm: boolean; limit?: number | null }) =>
    post<Job>("/api/eval/run", body),
};

/** POST a question and invoke onEvent for every NDJSON event as it streams in. */
export async function streamChat(
  projectId: number,
  question: string,
  learner: string | null,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`/api/projects/${projectId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, learner }),
      signal,
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(0, BACKEND_DOWN);
  }
  if (!response.ok || !response.body) throw await errorFrom(response);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line) as ChatEvent);
      newline = buffer.indexOf("\n");
    }
  }
  const rest = buffer.trim();
  if (rest) onEvent(JSON.parse(rest) as ChatEvent);
}
