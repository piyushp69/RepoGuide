export type JobStatus = "queued" | "running" | "done" | "error";

export interface Job {
  id: number;
  project_id: number | null;
  kind: string;
  job_key: string | null;
  status: JobStatus;
  phase: string | null;
  current: number;
  total: number;
  message: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Project {
  id: number;
  name: string;
  source_type: "local" | "github";
  source: string;
  root_path: string;
  branch: string | null;
  status: "new" | "indexing" | "ready" | "error";
  error: string | null;
  embed_model: string | null;
  index_version: number;
  file_count: number;
  chunk_count: number;
  skipped: Record<string, number>;
  created_at: string;
  updated_at: string;
  last_indexed_at: string | null;
  job: Job | null;
  has_overview: boolean;
}

export interface Health {
  status: string;
  version: string;
  ollama: {
    reachable: boolean;
    error: string | null;
    models: string[];
    chat_model_available: boolean;
    embed_model_available: boolean;
  };
  config: { chat_model: string; embed_model: string; ollama_base_url: string; num_ctx: number };
}

export interface ProjectFile {
  path: string;
  language: string;
  line_count: number;
  size: number;
  chunks: number;
}

export interface SymbolInfo {
  file_path: string;
  name: string;
  qualified_name: string;
  kind: string;
  parent: string | null;
  start_line: number;
  end_line: number;
  chunk_id: string | null;
  docstring: string | null;
}

export interface FileSlice {
  path: string;
  language: string;
  total_lines: number;
  start_line: number;
  end_line: number;
  view_start: number;
  view_end: number;
  lines: string[];
}

export interface RelatedRef {
  file_path: string;
  symbol: string | null;
  start_line: number;
  end_line: number;
  confidence?: number;
}

export interface Related {
  callees: RelatedRef[];
  callers: RelatedRef[];
  imports: { file_path: string }[];
  imported_by: { file_path: string }[];
}

export interface Source {
  n: number;
  chunk_id: string;
  file_path: string;
  start_line: number;
  end_line: number;
  symbol: string | null;
  symbol_type: string;
  language: string;
  via: string | null;
  score: number;
  ranks: Record<string, number>;
  content?: string;
}

export interface Citation {
  n: number;
  chunk_id: string;
  file_path: string;
  start_line: number;
  end_line: number;
  symbol: string | null;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  question: string | null;
  learner: string | null;
  citations: Citation[];
  sources: Source[];
  /** 1 = cites the code, 0 = the code does not contain the answer, -1 = no citations (unverified). */
  grounded: number | null;
  warnings: string[];
  created_at: string;
  feedback: { rating: number; comment: string | null } | null;
}

export type ChatEvent =
  | { type: "sources"; user_message_id: number; sources: Source[]; timings_ms: Record<string, number> }
  | { type: "token"; text: string }
  | { type: "rewrite"; message: string }
  | { type: "done"; message: ChatMessage }
  | { type: "error"; message: string; message_id?: number };

export interface TechItem {
  name: string;
  category: string;
  evidence: string[];
}

export interface ModuleInfo {
  path: string;
  description: string;
  files: { path: string; language: string; lines: number }[];
  lines: number;
  key_symbols: { name: string; kind: string; file: string; line: number; doc: string }[];
  depends_on: string[];
  used_by: string[];
}

export interface Overview {
  project: { name: string; files: number; lines: number; chunks: number; module_depth: number };
  summary: string;
  readme_path: string | null;
  languages: { language: string; files: number; lines: number }[];
  tech_stack: TechItem[];
  entry_points: { file: string; line: number | null; reason: string }[];
  modules: ModuleInfo[];
  execution_flow: { title: string; description: string; files: string[] }[];
  mermaid: string;
  graph: { internal_imports: number; external_imports: number; calls: number };
  model: string;
  warnings: string[];
  stale: boolean;
  generated_at: string;
}

export interface Role {
  id: string;
  label: string;
  focus: string;
}

export interface PathFile {
  path: string;
  why: string;
  language: string;
  lines: number;
  symbols: string[];
}

export interface PathStep {
  title: string;
  goal: string;
  files: PathFile[];
  quiz_id: number | null;
}

export interface LearningPath {
  id: number;
  project_id: number;
  role: string;
  role_label: string;
  role_fit: "good" | "limited";
  title: string;
  overview: string;
  steps: PathStep[];
  generated_at: string;
  stale: boolean;
}

export interface QuizQuestion {
  id: string;
  question: string;
  options: string[];
  file: string;
}

export interface Quiz {
  id: number;
  path_id: number;
  step_index: number;
  generated_at: string;
  questions: QuizQuestion[];
}

export interface QuizResult {
  score: number;
  total: number;
  passed: boolean;
  results: {
    id: string;
    chosen: number | null;
    correct: boolean;
    answer_index: number;
    explanation: string;
    file: string;
    line: number | null;
    evidence: string;
  }[];
}

export interface Progress {
  completed_steps: number[];
  quiz_scores: Record<string, { best: number; attempts: number }>;
}

export interface Learner {
  id: number;
  name: string;
  created_at: string;
}

export interface DashboardPath {
  path_id: number;
  project_id: number;
  project_name: string;
  role: string;
  role_label: string;
  steps_total: number;
  steps_completed: number;
  quiz_attempts: number;
  scores: { step_index: number; score: number; total: number; at: string }[];
  avg_quiz_score: number | null;
  progress: number;
}

export interface Dashboard {
  learners: {
    id: number;
    name: string;
    last_activity: string | null;
    questions_asked: number;
    paths: DashboardPath[];
    avg_quiz_score: number | null;
    steps_completed: number;
    steps_total: number;
  }[];
  top_questions: { question: string; count: number; learners: string[]; last_asked: string }[];
}

export interface FeedbackItem {
  id: number;
  project_id: number;
  message_id: number | null;
  learner: string | null;
  question: string;
  answer: string;
  retrieved_chunk_ids: string[];
  cited_chunk_ids: string[];
  rating: 1 | -1;
  comment: string | null;
  created_at: string;
  updated_at: string;
  eval_question: { id: number; expected_file: string; expected_symbol: string | null } | null;
}

export interface FeedbackStats {
  total: number;
  up: number;
  down: number;
  answers: number;
  satisfaction: number | null;
  by_day: { day: string; up: number; down: number }[];
}

export interface EvalQuestion {
  id: number;
  project_id: number;
  question: string;
  expected_file: string;
  expected_symbol: string | null;
  feedback_id: number | null;
  created_at: string;
}

export interface EvalMetrics {
  questions: number;
  hit_at_5: number | null;
  mrr: number | null;
  file_hit_at_5: number | null;
  file_mrr: number | null;
  in_context_rate: number | null;
  citation_validity?: number | null;
  citations_checked?: number;
  answers_with_citations?: number | null;
  answers_citing_expected_file?: number | null;
  answers_citing_expected_symbol?: number | null;
  answers_rewritten?: number | null;
  answers_with_corrected_citations?: number | null;
  avg_answer_seconds?: number | null;
  abstention_rate?: number | null;
  unanswerable_questions?: number;
}

export interface EvalRunSummary {
  run_id: string;
  created_at: string;
  eval_set: { name: string; description?: string; path?: string; questions: number; unanswerable: number };
  project: { id: number; name: string; root_path: string };
  config: { chat_model: string; embed_model: string; top_k: number; mrr_cutoff: number; with_llm: boolean };
  metrics: EvalMetrics;
  ablation: Record<string, { hit_at_5: number | null; mrr: number | null }>;
  duration_seconds: number;
}

export interface EvalCitationCheck {
  kind: "marker" | "mention";
  n?: number;
  file_path: string | null;
  start_line: number | null;
  end_line: number | null;
  valid: boolean;
  reason: string;
}

export interface EvalQuestionResult {
  id: string;
  question: string;
  expected_file: string;
  expected_symbol: string | null;
  symbol_found_in_index: boolean;
  rank: number | null;
  file_rank: number | null;
  hit_at_5: boolean;
  in_context: boolean;
  top5: { file_path: string; symbol: string | null; lines: string }[];
  answer?: string;
  citations?: EvalCitationCheck[];
  cites_expected_file?: boolean;
  seconds?: number;
  ablation: Record<string, { rank: number | null }>;
}

export interface EvalReport extends EvalRunSummary {
  questions: EvalQuestionResult[];
  unanswerable: { id: string; question: string; answer: string; abstained: boolean; seconds: number }[];
  files?: { json: string; markdown: string };
}
