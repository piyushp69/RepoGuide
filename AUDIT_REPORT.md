# RepoGuide audit report

Audit date: 2026-10-08. Every status below comes from running the feature on this machine, not from the
README or DECISIONS.md.

**Result:** all 11 features work after the fixes. Four were partial when the audit started (Indexing,
Overview, Evaluation, Frontend). The audit found and fixed 8 bugs plus 3 small lint issues and added 17
tests. Final results: 74 tests passed, 0 failed; smoke test 12/12; frontend build clean. Privacy holds: no
RepoGuide process connected anywhere except the local Ollama, and the only other network use is git for
the repository the user added.

## How the audit was run

- **Machine:** Windows 11 Home, RTX 4060 Laptop GPU (8 GB), Python 3.14.3 (project venv), Node 24,
  Ollama 0.40.0 with `qwen2.5-coder:7b` (Q4_K_M) and `nomic-embed-text`. `.env` points at
  `http://localhost:11434`.
- **Backend:** run through a wrapper that installs a Python audit hook (`sys.addaudithook`). The hook logs
  every socket connect, DNS lookup and subprocess started by any code in the process, third-party
  libraries included. The evaluation CLI was run under the same hook.
- **UI:** the real UI (Vite dev server), driven by headless Microsoft Edge over the DevTools protocol.
  The audit captured page loads, console errors, every network request and screenshots, and used request
  interception to test loading and error states.
- **Checks:** 138 scripted checks against the HTTP API, each recorded with its evidence (paths, line
  ranges, timings, model output).
- **Failure tests:**
  - **Ollama down:** a second backend pointed at a dead port (`OLLAMA_BASE_URL=http://localhost:11999`)
    on a copy of the database.
  - **Crash:** the server process was killed in the middle of embedding, then restarted.
- **Second real repository:** [fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template),
  cloned through the app (commit `f27b4721`, 221 files, FastAPI + SQLModel + React + TanStack). Its
  question set is `eval/eval_set_fullstack_template.json`: 21 questions plus 3 unanswerable. I wrote it by
  reading that commit's code by hand.
- **Audit scripts:** kept outside the repository (they were throwaway). The repository only gained the
  tests, the eval set and the fixes listed below.

## 1. Feature status

| # | Feature | At start | Final | Evidence (all run during the audit) |
|---|---|---|---|---|
| 1 | Ingestion (local folder, GitHub URL, filters) | WORKS | **WORKS** | Folder with spaces + non-ASCII names; `.gitignore`, `node_modules`, build, venv, binaries (extension + NUL sniff), >512 KB, lock files and secrets all skipped with counts. 4 path spellings (`\`, `/`, trailing slash) → one project. Relative, missing, file-not-folder, non-GitHub URL and empty input → 400 with a clear message. GitHub URL cloned + indexed (221 files, 1,113 chunks, 2,230 edges, 54 s). Nonexistent repo → job error `git clone failed: … not found`. |
| 2 | Parsing and chunking | WORKS | **WORKS** | Every chunk's path, line range and text checked against disk (76 + 8 chunks, 0 mismatches). Python classes split into header + methods with qualified names; TS classes/interfaces, TSX components, JS functions; Markdown by heading; other files as file/line windows. Python 3.14 `except A, B:` in the real repo parsed. |
| 3 | Code graph | WORKS | **WORKS** | Sample: 77 import + 61 call edges, used by retrieval expansion and the code viewer (callers/callees). Real repo: 895 frontend imports via the `@/` alias, 140 Python package imports, 803 call edges. |
| 4 | Indexing (embeddings, incremental, progress) | PARTIAL | **WORKS** | 76 chunks in 5 batched `/api/embed` calls. Unchanged re-index does no work; one edited file → 1 chunk re-embedded; deleted file removed from SQLite and the vector store. Progress phases visible. *At start:* a crash mid-index left the project stuck at "indexing" (bug 5). |
| 5 | Q&A (hybrid retrieval, citations, streaming, history) | WORKS | **WORKS** | Every citation checked against the file on disk. Streaming: 389 token events, first token about 3 s. Follow-ups use history; history is kept per project and learner. Caveat in section 6. |
| 6 | Abstention | WORKS | **WORKS** | Sample: 3/3 refused (Stripe, Kafka, Vue). Real repo: 3/3 in the eval set (Stripe, Kafka, WebSockets) plus an ad-hoc push-notifications question. |
| 7 | Overview + Mermaid | PARTIAL | **WORKS** | 2 real `/api/chat` calls per regeneration. Tech stack with evidence, entry points, module map, 7-step flow over real files; the Mermaid diagram renders in the browser (7 nodes). Real repo: 25 stack items, 24 modules all described, correct summary. *At start:* bugs 1, 3 and 7. |
| 8 | Onboarding (role paths, quizzes, progress, manager dashboard) | WORKS | **WORKS** | Frontend, full-stack and backend paths from real files and real model calls. Every quiz question's evidence found at the real line; answers hidden from the learner API. Progress (quiz pass + manual step) and the dashboard (progress, quiz scores, top questions) checked. |
| 9 | Feedback (+ eval set, JSONL export) | WORKS | **WORKS** | Thumbs up/down + comment stored with the question, retrieved chunk ids, answer and time. Stats by day. "Add to evaluation set" stores file + symbol. Export script wrote 2 training pairs. |
| 10 | Evaluation (harness, reports, UI) | PARTIAL | **WORKS** | Fixture: 15 files, 30 + 3 questions. Reported metrics recompute exactly from the per-question data. JSON + Markdown reports; runs listed on the Evaluation page. *At start:* bugs 6 and 8. |
| 11 | Frontend | PARTIAL | **WORKS** | All 7 pages load, call their APIs, and log no console errors. Loading and error states on every page. All 340 browser requests stayed on localhost. Production build clean. *At start:* bug 4. |

## 2. Bugs found and fixed

The smallest change that fixes each bug; nothing was rewritten and the stack is unchanged.

**1. An empty project got an invented description and an empty diagram box.**
- **Found:** adding an empty folder named "Audit empty folder" produced the summary "The Audit empty
  folder project is a tool designed to identify and report on empty folders…" and a Mermaid diagram with
  no nodes.
- **Cause:** `OverviewService.generate` always asked the model to summarise, even with zero files, so it
  only had the project name to go on. `build_mermaid` emitted a flowchart with only style definitions.
- **Fix:**
  - `overview/service.py`: no model call when nothing is indexed; a fixed factual summary instead ("No
    supported source files were found…").
  - `overview/mermaid.py`: returns `""` when there are no modules.
  - `OverviewPage.tsx`: shows "No folders with indexed source files, so there is no diagram to draw."
- **Verified:** a unit test asserts no model call. Live API: overview in 1.5 s with the factual text.
  Browser: the page shows the message and no diagram.

**2. "This project has not been indexed yet." was shown for projects that were indexed or still indexing.**
- **Found:** asking a question in an indexed project with no code, or during its first indexing, gave
  this message.
- **Cause:** `require_indexed` (`api/deps.py`) only looked at `chunk_count`.
- **Fix:** the message now depends on the project status:
  - still indexing: "still being indexed. Try again when indexing finishes."
  - indexed but no code: "was indexed but contains no supported source files to search."
  - otherwise: the original message.
- **Verified:** tests, plus live checks during a real indexing run and on an empty project.

**3. Regenerating the overview while Ollama was down erased the existing summary.**
- **Found:** on the Ollama-down backend, Regenerate replaced a good summary, the module descriptions and
  the execution flow with empty values. Only warnings were left.
- **Cause:** `generate()` started from empty strings and only appended a warning on `OllamaError`, then
  saved the result over the old overview.
- **Fix:** on a model failure, keep the previous summary, module descriptions and flow (flow files
  filtered to files that still exist). The warning says "Showing the previous summary, which may be out
  of date." The static parts are rebuilt as usual.
- **Verified:** 2 unit tests. Live: the dead-port backend regenerated 7 projects and every one kept its
  text and reported the failure.

**4. The UI said "502 Bad Gateway" when the backend was stopped.**
- **Found:** with the backend stopped and the UI served by Vite (what `start.ps1`/`dev.py` run), every
  page showed "502 Bad Gateway", and the header said "No projects yet".
- **Cause:** the Vite proxy answers 502 with an empty text body. `errorFrom` (`frontend/src/api/client.ts`)
  only had the friendly message for `fetch()` network errors, which never happen behind the proxy. The
  project picker treated "request failed" the same as "no projects".
- **Fix:**
  - `client.ts`: a non-JSON 502/503/504 maps to "Cannot reach the RepoGuide backend. Is it running?" The
    backend's own 503s carry a JSON `detail` and are unaffected.
  - `Layout.tsx`: the picker says "Projects unavailable" on error and "Loading projects…" while loading.
- **Verified:** headless Edge with the backend stopped shows the right banner and header text. A Retry
  button is shown, and the sidebar says "Backend not reachable".

**5. After a crash or restart, an interrupted project stayed "indexing" forever.**
- **Found:** killing the server mid-embedding left the project at status `indexing` with no error. The
  job had been marked failed; the project had not.
- **Cause:** the startup cleanup only reset the `jobs` table.
- **Fix:** `jobs.py`: the cleanup also sets such projects to `error` with "Indexing was interrupted because
  the server restarted. Re-index to finish." The Projects page shows that message with a re-index button.
- **Verified:** a test, plus a real crash: kill mid-embedding → restart → status `error` with the
  message → re-index resumed the 889 pending embeddings. No other data was lost (11 tables compared).

**6. Running `evaluate.py` in its own process could fail the server's live jobs.**
- **Found:** by reading the code while adding `--eval-set` support. Not reproduced before the fix.
- **Cause:** the crash cleanup ran in `JobManager.__init__`, so any process that built the services did
  it. That includes `evaluate.py --in-process` and `evaluate.py --eval-set`. Run while the server was
  working, it would have marked the server's running jobs as failed (and, after fix 5, its indexing
  projects too), and the UI would stop following them.
- **Fix:**
  - `jobs.py`: the cleanup moved to `JobManager.recover_interrupted()`.
  - `api/app.py`: only the server calls it, once at startup.
- **Verified:** a test that a second `JobManager` leaves running jobs alone, and a test of the server
  start-up path. Live: an in-process `evaluate.py` ran while the server was embedding 4,500 chunks; the
  job stayed `running` and finished `done`.

**7. Overview summaries invented a purpose from the project name.**
- **Found:** a 7-line Go program that prints "hi", registered as "Audit no supported languages", was
  summarised as "an audit tool that checks for unsupported languages in codebases…". A file of number
  doubling functions in "Audit big file" was described as being "for developers who need to perform this
  operation on large files".
- **Cause:** the summary prompt began with `Project name: …`. With little code to go on, the 7B model
  wrote the description around the name.
- **Fix:** `overview/service.py`: the name is no longer sent; a real project name still reaches the
  model through the README.
- **Verified:** with the real model, before and after on both small projects ("a Go application…",
  "Python functions that double the value of numbers…"). The two real projects still get correct
  summaries ("ShopLite is a small online shop with a Flask backend and a React frontend…"; "a
  full-stack application built with FastAPI … and React … user authentication, email-based password
  recovery…").

**8. The evaluation reported a correct file mention as "file does not exist".**
- **Found:** the real repo's first run reported citation validity 0.975. The one "invalid" citation was
  the answer's mention of `main.py`, and the repo has two `main.py` files.
- **Cause:** `resolve_mention` returned "no match" when a bare file name matched several files, and the
  harness treated that as a nonexistent file. The chat UI's own warning logic already skips bare names
  as ambiguous.
- **Fix:** `evaluation/metrics.py` gained `mention_matches` and `check_mention`. A shared name is valid
  if it fits one of the matching files (line range included) and is labelled "name shared by N files".
  A name that matches nothing is still invalid. `evaluation/service.py` uses it.
- **Verified:** a unit test (shared name, a line range that singles out one file, a range that fits
  neither, a missing file). Re-run on the real repo: validity 1.000 over 41 checks; the mention is
  labelled `main.py … (name shared by 2 files)`.

**Lint** (ruff with correctness rules, run from a throwaway venv because ruff is not part of the
project):
- `graph/resolver.py`: unused loop variable.
- `index/vectorstore.py`: `zip()` without `strict=True`. The ids and distances must line up.
- `tests/test_evaluation.py`: unused import.

The remaining ruff output is style only: import order, pyupgrade, and FastAPI's `Depends()` default
argument pattern (B008, a false positive). I left those alone.

**Added for the audit:** `evaluate.py --project-id N --eval-set FILE` runs a hand-written question set
against any indexed project. It was needed to measure the second repository, and it is documented in the
README.

## 3. Hidden problems (Step 3)

**Stubs, TODOs, mock data, placeholders.**
- Scanned the app code for TODO, FIXME, HACK, mock, dummy, stub, lorem, NotImplemented, fake and
  placeholder values. The only hit is the docstring of `build_services`, which mentions the test fakes.
- `FakeLLM` and `FakeEmbedder` live in `backend/tests/fakes.py` and are only imported by tests.
- The UI has no hard-coded numbers: every number on the Evaluation page comes from `eval/results/*.json`.

**Privacy (network calls).**
- **Server:** across all audited server sessions, every connection went to `localhost:11434` (Ollama) or
  to the dead port used for the Ollama-down test. The rest were loopback socket pairs from asyncio.
- **CLI:** the in-process evaluation runs connected only to `localhost:11434`.
- **Static:** only `llm/ollama.py` imports an HTTP client and only `ingest/sources.py` starts processes
  (git). The only other URLs in the code are user-facing install hints, an input placeholder and the
  GitHub clone URL.
- **Config:** a non-local `OLLAMA_BASE_URL` (`example.com`, a LAN IP, a cloud API) is refused at startup.
- **Chroma:** telemetry is off (`anonymized_telemetry=False`, checked in a test).
- **Browser:** all 340 requests went to localhost.
- **Guard:** `tests/test_privacy.py` now locks these rules in.
- **Nothing had to be removed.** One thing to know: re-indexing a GitHub project runs `git fetch --depth 1`
  and `git reset` against the same repository it was cloned from. I consider that part of "git clone of a
  repo the user provides", but it is a fetch, not only a clone.

**Are numbers real?**
- **Overview:** text comes from real model calls (2 `/api/chat` calls per regeneration). With Ollama
  down, no text is produced and only the static facts change.
- **Quizzes:** every question's evidence was found at the cited line on disk.
- **Evaluation:** metrics recompute exactly from the per-question records (hit@5 0.9333, MRR 0.7559).
- **Ablation:** produced by running each retriever separately.

**Error handling.**

| Situation | Behaviour seen |
|---|---|
| Ollama stopped | Health says unreachable. Chat: error event "Cannot reach Ollama at http://localhost:11999. Is `ollama serve` running?", saved as a failed answer, never a fake one. Indexing, onboarding and evaluation jobs fail with that message. Overview keeps its static facts and (after fix 3) its previous text. |
| Invalid repo URL | Non-GitHub URL → 400. Nonexistent GitHub repo → index job error `git clone failed: … not found`. |
| Empty folder | Indexes (0 files); factual overview; chat → 409 "contains no supported source files" (fixes 1, 2). |
| No supported languages | Go + text files indexed as line windows; a question about deployment was answered from `notes.txt`, citing it. |
| Very large file | Over 512 KB: skipped and counted. A 418 KB file with 4,500 functions: 4,500 chunks in 92 s. |
| Question before indexing finishes | 409 "still being indexed" (fix 2); works once indexing is done. |
| Backend stopped (UI) | "Cannot reach the RepoGuide backend. Is it running?" with Retry (fix 4). |
| Server crash mid-index | Job marked failed, project marked error with a re-index hint (fix 5); nothing else lost. |

**Windows/Unix paths.** Backslashes, forward slashes and a trailing slash resolve to the same project.
Stored paths are project-relative POSIX paths. Folder and file names with spaces and non-ASCII
characters work. Git-Bash-style `/d/...` paths are rejected on Windows with a clear message.

**Database.**
- All 15 tables are created.
- Data survives restarts (row counts compared across a crash and restart).
- Deleting a project leaves no orphan rows in any table and drops its vector collection; checked when
  the audit's own projects were deleted.

**Performance.**
- **Batching:** embeddings go in batches. Benchmark on this GPU: batch 1 → 15 chunks/s, 16 → 39/s,
  32 → 37/s, 64 → 37/s; the default of 16 is the best.
- **Incremental:** nothing is re-embedded unless it changed.
- **UI during indexing:** stays responsive. 190 API polls during a big index: median 28 ms, p95 65 ms,
  max 109 ms.

## 4. Tests, build, lint

| Check | Result |
|---|---|
| `pytest` (unit, API, scripts, real-Ollama integration) | **74 passed, 0 failed, 0 skipped** (31 s). The original 57 all pass. |
| Tests added | `test_robustness.py` (8), `test_privacy.py` (8), `test_evaluation.py` (+1) |
| `scripts/smoke_test.py` against the running app with real models | **12/12 passed** |
| `npm run build` (`tsc -b` strict with `noUnusedLocals`/`noUnusedParameters`, then `vite build`) | 0 errors, 0 warnings |
| Frontend lint | No ESLint config in the project; strict TypeScript is the lint step and passes. |

## 5. Evaluation numbers

### Sample repository (`eval/eval_set.json`, 30 questions + 3 unanswerable)

| Metric | Before the audit (run `20261007-212439`) | Final (run `20261007-231656`) |
|---|---|---|
| Hit rate @5, expected symbol / file | 0.933 / 1.000 | **0.933 / 1.000** |
| MRR @10, expected symbol / file | 0.756 / 0.889 | **0.756 / 0.889** |
| Relevant chunk in answer context | 1.000 | 1.000 |
| Citation validity | 1.000 (65) | **1.000 (64 checked)** |
| Answers with a citation / citing expected file / expected symbol | 1.000 / 0.967 / 0.933 | 1.000 / 0.967 / 0.900 |
| Abstention on unanswerable questions | 3 / 3 | **3 / 3** |
| Ablation hit@5 / MRR: vector, BM25, hybrid | 0.933 / 0.800, 0.767 / 0.689, 0.933 / 0.756 | identical |
| Average answer time | 1.7 s | 1.9 s |

Retrieval numbers match exactly; the audit did not change retrieval or the Q&A prompts. The answer-side
numbers vary slightly from run to run (see section 6).

### Second repository: fastapi/full-stack-fastapi-template (21 questions + 3 unanswerable)

| Metric | Final (run `20261007-231750`) |
|---|---|
| Hit rate @5, expected symbol / file | **0.857 / 0.857** |
| MRR @10, expected symbol / file | **0.632 / 0.708** |
| Relevant chunk in answer context | 0.857 |
| Citation validity | **1.000 (41 checked)**; the first run showed 0.975 because of bug 8 |
| Answers with a citation / citing expected file / expected symbol | 1.000 / 0.857 / 0.809 |
| Abstention (Stripe, Kafka, WebSockets) | **3 / 3** |
| Ablation hit@5 / MRR: vector, BM25, hybrid | 0.762 / 0.438, 0.619 / 0.477, **0.857 / 0.632** |
| Average answer time | 2.1 s |

Observations on the real repository:

- **It works beyond the fixture.** Clone, parse and embed took 54 s. The graph resolved TS `@/` alias and
  Python package imports. The overview's tech stack (25 items with evidence), entry points, module
  descriptions and summary are correct. A frontend onboarding path used real files (`main.tsx`, the theme
  provider, the UI components, `AddUser.tsx`). Chat answers about auth, password hashing, the current user
  and login timing attacks cited the right lines.
- **Hybrid search matters more here.** On real code it beats vector-only by 0.095 hit@5 and BM25-only by
  0.238. On the sample set it was only level with vector-only.
- **Misses (3 of 21):**
  - "What stops a normal user from calling admin-only endpoints?": four `test_users.py` tests and the
    `Admin` React component outranked `deps.get_current_active_superuser`.
  - "What checks are made when users change their own password?": the frontend `ChangePassword` form and
    E2E tests outranked `users.update_password_me` (rank 9). The answer described the real frontend
    checks but missed the backend ones.
  - "How long are access tokens valid by default…?": `config.Settings` was not retrieved.
- **One wrong answer with a valid-looking citation.** The token question was answered "30 minutes, as
  configured in `settings.ACCESS_TOKEN_EXPIRE_MINUTES` [1]". The real default is `60 * 24 * 8` minutes
  (8 days). The cited `login.py:23-42` is real and uses the setting but does not contain any value. The
  model filled the gap from general FastAPI knowledge. Citation validity cannot catch this because it
  checks that cited lines exist, not that they support the sentence.
- **Abstention held:** all three questions about absent technologies were refused.

## 6. Still broken or partial

Nothing is broken or missing. These limitations remain, each with the reason it was not changed:

1. **Answers can contain unsupported details** (the "30 minutes" case above, 1 of 21 answers on the real
   repo). This is a limit of the 7B model when retrieval misses the defining code. Fixing it needs a
   claim-checking feature (improvement 1), not a small bug fix. DECISIONS.md already lists it as a known
   limitation.
2. **Ranking on real repos favours tests and frontend code** over the backend definition (3 misses
   above). This is tuning work. It needs a larger real-repo question set first, or the weights will just
   be fitted to these 21 questions.
3. **Empty projects created before fix 1 keep their invented summary.** The overview is only regenerated
   when the code changes, and Regenerate is refused (409) for projects without code. The only such
   project was the audit's own fixture, now deleted. I didn't relax the 409 rule for this one edge case.
4. **Answer-side metrics vary slightly between runs** even at temperature 0. Citations checked were
   64–67 across three runs of the sample set, and "citing the expected symbol" was 0.900 vs 0.933 in the
   README's run. Retrieval metrics were identical in every run. The cause is floating-point
   nondeterminism in GPU generation, so differences of a few hundredths between single runs are not
   regressions.
5. **Languages without a tree-sitter grammar** (Go, Java, C#, …) are indexed as line windows. They get
   no symbols, call graph or key symbols. This is documented and worked as designed in the audit.

## 7. Three most important improvements

1. **Check answers' claims against the cited code.**
   - **What:** flag numbers, identifiers and file names in an answer that appear in none of the cited
     excerpts, show the flag in the chat, and add "supported claims" to the evaluation.
   - **Why:** it would have caught "30 minutes", the failure that matters most to someone learning a
     codebase.
2. **Improve ranking on real codebases.**
   - **What:** down-weight tests, E2E specs and generated client code (`sdk.gen.ts`) unless the question
     asks about them. Boost config and settings files for "default / configured / environment"
     questions.
   - **How:** grow the second-repo set to about 50 questions (and add a non-Python/TypeScript repo) so
     the tuning can be measured rather than fitted. All 3 misses on the real repo were ranking misses.
3. **Add tree-sitter grammars for more languages** (Go, Java, C#, Rust). Many real repositories mix
   languages, and today everything outside Python/JS/TS loses symbols, the call graph and good entry
   points. That weakens retrieval, the overview and onboarding for those files.

## 8. Files changed by the audit

**Backend**
- `backend/repoguide/jobs.py`: fixes 5 and 6.
- `backend/repoguide/api/app.py`: fix 6.
- `backend/repoguide/api/deps.py`: fix 2.
- `backend/repoguide/overview/service.py`: fixes 1, 3 and 7.
- `backend/repoguide/overview/mermaid.py`: fix 1.
- `backend/repoguide/evaluation/metrics.py`, `backend/repoguide/evaluation/service.py`: fix 8.
- `backend/repoguide/graph/resolver.py`, `backend/repoguide/index/vectorstore.py`: lint.
- `backend/scripts/evaluate.py`: `--eval-set` with `--project-id`.

**Tests**
- `backend/tests/test_robustness.py` (new).
- `backend/tests/test_privacy.py` (new).
- `backend/tests/test_evaluation.py`: new test, lint.

**Frontend**
- `frontend/src/api/client.ts`: fix 4.
- `frontend/src/components/Layout.tsx`: fix 4.
- `frontend/src/pages/OverviewPage.tsx`: fix 1.

**Data and docs**
- `eval/eval_set_fullstack_template.json` (new).
- `eval/results/`: the audit's runs, `20261007-230708` to `20261007-231750`.
- `README.md`: evaluation commands, second-repo results.
- `DECISIONS.md`: restart cleanup, overview prompt, keeping text when the model is down.
- `AUDIT_REPORT.md` (this file).

## 9. Data the audit left in the app

- **Deleted:** the nine projects the audit created to test edge cases (empty folder, big file, no
  supported languages, etc.), removed through the app's own delete endpoint. Their folders were in a
  temporary directory.
- **Kept on purpose:** `fastapi/full-stack-fastapi-template`, cloned in `data/workspace/github/` and used
  for the second-repo evaluation. Delete it from the Projects page if you don't want it.
- **Still in the sample project:** test activity from the audit and smoke test. This is a learner named
  "Audit Learner" with chat history, 3 feedback records, 1 feedback-derived evaluation question, a
  learning path, quiz attempts and step progress, alongside the earlier "Smoke Test" learner.
