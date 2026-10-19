# RepoGuide

A private, self-hosted codebase explainer and onboarding tool. Point it at a local folder or a
GitHub repository and it will:

1. **Explain the project**: plain-language summary, tech stack, entry points, a module-by-module map
   ("what lives where"), the main execution flow and a Mermaid architecture diagram.
2. **Answer questions about the code** with file and line citations. Click a citation to open the
   exact lines, with their callers and callees from the code graph. If the indexed code does not
   contain the answer, it says so instead of guessing.
3. **Onboard new developers**: role-based learning paths (frontend, backend, data/ML, full-stack),
   multiple-choice quizzes grounded in the actual code, per-learner progress tracking and a manager
   dashboard.
4. **Collect feedback** (thumbs up/down + comment) on every answer, turn thumbs-down questions into
   evaluation cases, and export JSONL training pairs for a future reranker.
5. **Measure its own accuracy** with an evaluation harness (hit rate @5, MRR, citation validity,
   abstention), with results and history shown in the UI.

**Everything runs on your machine.** All model calls go to Ollama on `localhost:11434` (the backend
refuses any other host); there is no telemetry (ChromaDB's is disabled) and the UI loads nothing
from the internet (fonts, Mermaid and syntax highlighting are bundled). The only network use is
package installation, `ollama pull`, and `git clone` of repositories you add.

---

## Requirements

| | Tested with | Minimum |
|---|---|---|
| OS | Windows 11 | Windows 10+, macOS, Linux (scripts provided for both) |
| Python | 3.14.3 | 3.11 |
| Node.js / npm | 24.21 / 12.0 | Node 20 |
| Ollama | 0.40.0 | a version with `/api/embed` and structured outputs |
| Git | 2.52 | only needed for GitHub repositories |
| GPU | RTX 4060 Laptop, 8 GB VRAM | runs on CPU too, much slower |

Models (pulled by the setup script, configurable in `.env`):

- **Chat:** `qwen2.5-coder:7b` (Q4, about 4.7 GB on disk, 5.0 GB VRAM at an 8K context)
- **Embeddings:** `nomic-embed-text` (274 MB)

Both fit together in 8 GB of VRAM (`ollama ps` shows 100% GPU for both).

## Quick start

Windows (PowerShell):

```powershell
.\setup.ps1      # venv + Python packages, npm packages, .env, ollama pull (one time)
.\start.ps1      # backend http://127.0.0.1:8000 + frontend http://127.0.0.1:5173
```

macOS / Linux:

```bash
./setup.sh
./start.sh
```

Open **http://127.0.0.1:5173**, go to **Projects** and add an absolute folder path (for example the
bundled sample app, `<repo>/eval/sample_repo`) or a GitHub URL such as
`https://github.com/owner/repo` (optionally `/tree/<branch>`). Indexing, embedding, the code graph and
the overview are built in the background with a progress bar.

`start.ps1` / `start.sh` run `scripts/dev.py`, which starts both servers and stops both on Ctrl+C.
For a single-port production build: `.\start.ps1 --prod` (builds the UI and serves it from the
backend at http://127.0.0.1:8000). Interactive API docs: http://127.0.0.1:8000/docs.

Manual equivalent of the scripts:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.txt
cd frontend; npm ci; cd ..
copy .env.example .env
ollama pull qwen2.5-coder:7b; ollama pull nomic-embed-text
cd backend; ..\.venv\Scripts\python -m repoguide        # API on :8000
cd frontend; npm run dev                                  # UI on :5173 (proxies /api)
```

## Everyday commands

| Task | Command (from the repository root) |
|---|---|
| Run all tests (unit, API, scripts, real-Ollama integration) | `cd backend; ..\.venv\Scripts\python -m pytest` |
| Tests without Ollama | `cd backend; ..\.venv\Scripts\python -m pytest -m "not integration"` |
| Evaluation (sample repo, with answers) | `.venv\Scripts\python backend\scripts\evaluate.py` |
| Evaluation, retrieval metrics only (seconds) | `.venv\Scripts\python backend\scripts\evaluate.py --no-llm` |
| Evaluate a project's feedback-derived set | `.venv\Scripts\python backend\scripts\evaluate.py --project-id 2` |
| Evaluate any indexed project with your own question set | `.venv\Scripts\python backend\scripts\evaluate.py --project-id 2 --eval-set my_questions.json` |
| Export feedback as JSONL training pairs | `.venv\Scripts\python backend\scripts\export_feedback.py [--project-id N] [--out file.jsonl]` |
| End-to-end smoke test against the running app (real models) | `.venv\Scripts\python scripts\smoke_test.py` |
| Type-check / build the UI | `cd frontend; npm run typecheck; npm run build` |

(On macOS/Linux use `.venv/bin/python` and forward slashes.) When the backend is running,
`evaluate.py` starts the run through its API so only one process writes to the vector store. A run with
`--eval-set` happens in the script's own process; combined with `--project-id` it only reads the index, so
it is safe while the backend runs.

## Pages

| Page | What it does |
|---|---|
| **Projects** | Add a folder or GitHub URL, re-index (incremental) or rebuild, switch, delete; live progress and skipped-file statistics. |
| **Overview** | Summary, tech stack (with evidence), entry points, Mermaid architecture diagram, execution flow, module map with key symbols. |
| **Chat** | Streaming answers with clickable `[n]` citations, the excerpts used, "not found" and "unverified" markers, thumbs up/down with comments; history per project and learner. |
| **Onboarding** | Pick a role, generate an ordered path (files to read and why), mark steps done, take 3-question quizzes; passing (60%+) completes the step. |
| **Manager** | (toggle "Manager view") learners, steps completed, quiz scores, most-asked questions. |
| **Feedback** | Ratings over time, thumbs-down triage, "Add to evaluation set" with the correct file and symbol, JSONL export. |
| **Evaluation** | Run the harness, latest metrics with deltas, history chart, retriever ablation, per-question drill-down. |

There is no authentication by design: a learner picks their name in the top bar (stored in the
browser), and the manager view is a toggle.

## Architecture

```mermaid
flowchart LR
  subgraph UI["React + Vite + Tailwind (frontend/)"]
    pages["Projects · Overview · Chat · Onboarding · Manager · Feedback · Evaluation"]
  end
  subgraph API["FastAPI (backend/repoguide/)"]
    ingest["ingest/: sources (local, git clone), walker (.gitignore, filters)"]
    parse["parsing/: tree-sitter chunker (Py/JS/TS) + line windows / Markdown sections"]
    graph["graph/: import + call graph resolver"]
    index["index/: incremental indexer, BM25, Chroma store"]
    retr["retrieval/: hybrid (vector + BM25 + symbol) RRF + graph expansion"]
    qa["qa/: grounded answers, citations, self-check"]
    ov["overview/ · onboarding/ · feedback/ · evaluation/"]
    jobs["jobs.py: background jobs + progress"]
  end
  sqlite[("SQLite: projects, files, chunks, symbols, edges, chat, paths, quizzes, progress, feedback")]
  chroma[("ChromaDB (persistent): chunk vectors")]
  ollama{{"Ollama :11434 (qwen2.5-coder:7b, nomic-embed-text)"}}
  pages -- "/api (JSON, NDJSON stream)" --> API
  ingest --> parse --> graph --> index
  index --> sqlite
  index --> chroma
  retr --> chroma
  retr --> sqlite
  qa --> retr
  index -. embeddings .-> ollama
  qa -. chat .-> ollama
  ov -. structured JSON .-> ollama
```

### Indexing pipeline

1. **Ingestion** (`ingest/`): a local folder is indexed in place; a GitHub URL is shallow-cloned into
   `data/workspace/github/` (re-indexing fetches and resets to the latest commit). The walker honours
   nested `.gitignore` files and skips dependency/build folders (`node_modules`, `dist`, `build`,
   `target`, …), virtual environments (any folder with `pyvenv.cfg`), lock files, binaries (by
   extension and by NUL-byte sniffing), minified/generated files, data dumps, secrets (`.env`, keys,
   certificates) and files over `MAX_FILE_SIZE_KB`.
2. **Parsing & chunking** (`parsing/`): tree-sitter splits Python, JavaScript and TypeScript (incl.
   JSX/TSX) into function, method and class chunks. Small classes (≤ 60 lines) stay whole; larger ones
   become a header chunk plus one chunk per method. Top-level code (imports, constants, `__main__`
   blocks) becomes "module" chunks, decorators and doc comments stay with their definition, and
   functions over 120 lines are split at blank lines. Markdown is split by heading; every other text
   file uses 60-line windows with 10 lines of overlap. Every chunk stores file path, start/end line,
   symbol name, symbol type and language.
3. **Code graph** (`graph/`): imports are resolved like Python and Node/TypeScript do (relative
   imports, packages, `src/` roots, `index` files, `tsconfig` `paths`, `@/` aliases, barrel
   re-exports); external packages are recorded for the tech stack. Calls are resolved best-effort:
   local and imported names, `self`/`this` methods, module aliases and class methods with high
   confidence, otherwise a name that is unique in the project with lower confidence.
4. **Embedding** (`index/`): chunks are embedded with the task prefixes nomic-embed-text expects
   (`search_document:` / `search_query:`) and a short header (path, symbol), then stored in ChromaDB
   (one collection per project, cosine distance).
5. **Incremental updates**: every file is SHA-256 hashed; unchanged files are skipped, changed files are
   re-chunked, and chunks whose content and position did not change keep their vectors (content-
   addressed chunk IDs), so editing one function re-embeds one chunk. Deleted files are removed from
   SQLite and Chroma. Changing `EMBED_MODEL` triggers a full re-embed automatically.

### Answering a question

1. **Retrieve**: the question goes to vector search and BM25 (identifier-aware tokens: `snake_case` and
   `camelCase` are split, words are stemmed). Identifiers or file names mentioned in the question are
   looked up directly. The project's own name and phrases like "this project" are removed from the
   search query, because they match everything.
2. **Fuse**: reciprocal-rank fusion (k = 60; BM25 weighted 0.5, see DECISIONS.md), deduplicated. Each
   retriever's best hit is guaranteed a place in the top 8.
3. **Expand**: the graph adds up to six neighbours of the top results (callees, callers, imported
   symbols), labelled for the model ("called by compute_order_total").
4. **Generate**: numbered excerpts (`[n] path:start-end — symbol`) go to the local model with strict
   rules: only use the excerpts, cite `[n]` after each statement, otherwise reply "I could not find
   this in the indexed code." Answers stream to the browser as NDJSON.
5. **Check**: `[n]` markers are mapped to file and line ranges. A draft that cites nothing is regenerated
   once with a stricter instruction; if it still cites nothing it is labelled *unverified*. A marker that
   points at the wrong excerpt is re-pointed when the sentence names a function that only another
   excerpt contains (and the correction is shown). File paths mentioned in the text that do not exist
   are reported as warnings, and cut-off answers are marked.

### Overview, onboarding, quizzes

- The **overview** combines static analysis (manifests, imports, entry-point heuristics, module
  grouping, import graph) with three short LLM calls constrained by JSON schemas: summary and module
  descriptions, then the execution flow traced from call chains starting at the entry points. The
  Mermaid diagram is generated deterministically from the module import graph, so its syntax is
  always valid.
- **Learning paths**: files are scored per role (paths, file types, imported frameworks, graph
  centrality, entry points), and the model orders the top candidates into 3-6 steps with a reason per
  file. Paths that are not in the candidate list are dropped.
- **Quizzes**: the model writes 3 questions from the step's code, each with an evidence line that must
  be found verbatim in the files (questions that fail this check are discarded). Options are
  shuffled, answers are only revealed after submission, and a score of 60% or more completes the step.

### Data

`data/` (git-ignored) holds `repoguide.db` (SQLite, WAL mode), `chroma/` (vectors) and
`workspace/` (clones). Evaluation reports are written to `eval/results/` as JSON and Markdown
(`latest.json`, `latest.md` and one pair per run).

## Evaluation

`eval/sample_repo/` is a 15-file mini shop (Flask + SQLite API, React/TypeScript checkout UI) used as
a test fixture. `eval/eval_set.json` has 30 questions with the expected file and symbol (26 phrased in
natural language, 4 using exact identifiers or error strings) plus 3 questions the code cannot answer.

Metrics (definitions in `backend/repoguide/evaluation/`):

- **Hit rate @5**: a top-5 chunk overlaps the expected symbol's lines in the expected file. Reported at
  symbol level (strict) and file level.
- **MRR @10**: mean reciprocal rank of the first relevant chunk.
- **Citation validity**: every `[n]` citation and every file path mentioned in an answer must exist,
  with line ranges inside the file and matching the indexed code. Out-of-range `[n]` markers count as
  invalid.
- Also reported: relevant code in the answer context, answers with citations, answers citing the
  expected file, drafts rewritten, abstention on unanswerable questions, and a vector-only vs
  BM25-only vs hybrid ablation.

Results on an RTX 4060 Laptop GPU with `qwen2.5-coder:7b` + `nomic-embed-text` (run
`20261007-212439`, after a clean reinstall; every run is kept in `eval/results/`, the newest in `latest.md`):

| Metric | Value |
|---|---|
| Hit rate @5, expected symbol / expected file | 0.933 / 1.000 |
| MRR @10, expected symbol / expected file | 0.756 / 0.889 |
| Citation validity (65 citations checked) | 1.000 |
| Answers with a citation / citing the expected file / the expected symbol | 1.000 / 0.967 / 0.933 |
| Abstention on unanswerable questions | 3 / 3 |
| Retriever ablation, hit@5 (vector / BM25 / hybrid) | 0.933 / 0.767 / 0.933 |

On this paraphrase-heavy set, vector-only search has a slightly higher MRR (0.800) than hybrid. BM25 is
kept, at half weight, for exact identifiers, error strings and config keys; see DECISIONS.md.

On a real repository, [fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)
(221 files, 1,113 chunks; `eval/eval_set_fullstack_template.json`, run `20261007-231750`): hit rate @5
0.857, MRR 0.632 (expected file: 0.857 / 0.708), citation validity 1.000 over 41 checks, abstention 3 / 3.
Here hybrid clearly beats vector-only (0.762) and BM25-only (0.619). The misses were questions where test
files or frontend code outranked the backend function, and one answer stated a wrong default (30 minutes
instead of 8 days) while citing a real line that does not contain the value; see AUDIT_REPORT.md.

## Project layout

```
backend/
  repoguide/            FastAPI app and all services (see Architecture)
    api/                routers: projects, chat/feedback, learning, evaluation
  scripts/              evaluate.py, export_feedback.py
  tests/                pytest: chunking, ingest, graph, retrieval, API, evaluation, scripts, Ollama
  requirements.txt
frontend/               React 19 + Vite 8 + Tailwind 4 + TypeScript
eval/
  sample_repo/          evaluation fixture (15 files)
  eval_set.json         30 questions + 3 unanswerable
  eval_set_fullstack_template.json   21 + 3 questions for github.com/fastapi/full-stack-fastapi-template
  results/              JSON + Markdown reports
scripts/                setup.py, dev.py (cross-platform)
setup.ps1 / setup.sh / start.ps1 / start.sh
.env.example
```

## Configuration

All settings live in `.env` (see `.env.example` for every option with its default):

| Setting | Default | Notes |
|---|---|---|
| `CHAT_MODEL` | `qwen2.5-coder:7b` | any Ollama chat model; 7B Q4 fits 8 GB VRAM |
| `EMBED_MODEL` | `nomic-embed-text` | changing it re-embeds projects on the next index |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | must be a loopback address |
| `LLM_NUM_CTX` | `8192` | context window for the chat model |
| `QA_TEMPERATURE` / `QA_MAX_TOKENS` | `0.0` / `1024` | answer decoding |
| `MAX_FILE_SIZE_KB` / `MAX_FILES` | `512` / `5000` | ingestion limits |
| `RETRIEVAL_TOP_K` / `CONTEXT_CHAR_BUDGET` | `8` / `16000` | excerpts per answer |
| `DATA_DIR` | `data` | SQLite, Chroma, clones |

## Performance (RTX 4060 Laptop GPU, 24 GB RAM)

- Sample repo (15 files): 76 chunks, 138 graph edges, indexed in about 15 s including model load.
- RepoGuide's own repository (122 files): 911 chunks and 1,973 edges, indexed with overview in 36 s.
- Parsing stress test, CPython 3.14 standard library (2,284 files): 62,110 chunks and 62,856 symbols
  in 13 s, 99k graph edges in 5.5 s (parsing only).
- Answers: about 2 s on average in the evaluation (non-streamed); 4-9 s end to end in the chat for
  longer explanations, with tokens streaming as they are generated.

## Troubleshooting

- **"Ollama not running" in the sidebar**: start the Ollama app or `ollama serve`, then reload.
- **"Model missing"**: `ollama pull qwen2.5-coder:7b` and `ollama pull nomic-embed-text` (or the models in your `.env`).
- **Port in use**: change `BACKEND_PORT` / `FRONTEND_PORT` in `.env`.
- **Private GitHub repositories**: clone them yourself and add the local folder (RepoGuide never prompts for credentials).

## Known limitations

See the end of DECISIONS.md for the full list. The main ones: a 7B model can still misread code while
citing the right lines (citation validity checks that citations point at real code, not that every
sentence is correct); the call graph is static and best-effort (no dynamic dispatch, decorators that
replace functions, or inheritance chains); tree-sitter chunking covers Python, JavaScript and TypeScript,
while other languages use line windows; the evaluation set is small (30 questions on one small app),
so its numbers are indicative rather than a benchmark.
