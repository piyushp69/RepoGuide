# RepoGuide project status report

Report date: 2026-10-19.

RepoGuide is functionally complete: every feature its README promises is implemented, all 74 backend tests
pass against the real local models, and the frontend type-checks with zero errors. What remains are
repository-hygiene gaps, quality limits the project already documents, and two small code defects.

## What's already built

Every feature area in the README is real code, not a stub: about 6,700 lines of Python in `backend/repoguide/`
and 4,100 lines of TypeScript in `frontend/src/`, with no TODOs or placeholder functions.

| Area | What it does | Where it lives |
|---|---|---|
| Ingestion | Indexes a local folder or shallow-clones a GitHub URL. Honours nested `.gitignore` and skips dependencies, build output, virtual envs, lock files, binaries, minified files, secrets and files over 512 KB. | `backend/repoguide/ingest/` |
| Parsing and chunking | tree-sitter splits Python, JavaScript and TypeScript into function, method and class chunks. Markdown splits by heading; other files use 60-line windows. | `backend/repoguide/parsing/` |
| Code graph | Resolves imports (relative, packages, tsconfig paths, `@/` alias, barrel files) and calls, with a confidence score per edge. | `backend/repoguide/graph/resolver.py` |
| Incremental indexing | Hashes each file; re-embeds only changed chunks; removes deleted files; reports progress by phase. | `backend/repoguide/index/` |
| Hybrid search | Vector search (ChromaDB) + BM25 keyword search + symbol lookup, merged by reciprocal-rank fusion, plus up to 6 neighbours from the code graph. | `backend/repoguide/retrieval/hybrid.py` |
| Q&A with citations | Streams answers with `[n]` citations to file and line. Says "I could not find this" when the code lacks the answer, rewrites uncited drafts once, and flags unverified answers. | `backend/repoguide/qa/` |
| Project overview | Summary, tech stack with evidence, entry points, module map, execution flow and a Mermaid architecture diagram. | `backend/repoguide/overview/` |
| Onboarding | Learning paths for 4 roles, 3-question quizzes whose evidence must appear word for word in the code, per-learner progress, manager dashboard. | `backend/repoguide/onboarding/` |
| Feedback | Thumbs up/down and comments on answers; thumbs-down questions become evaluation cases; JSONL training-pair export. | `backend/repoguide/feedback/`, `backend/scripts/export_feedback.py` |
| Evaluation | Hit rate @5, MRR @10, citation validity, abstention and a retriever comparison, saved as JSON and Markdown reports. | `backend/repoguide/evaluation/`, `backend/scripts/evaluate.py` |
| Web UI | 7 pages (Projects, Overview, Chat, Onboarding, Manager, Feedback, Evaluation), a code viewer showing callers and callees, light and dark themes. | `frontend/src/` |
| Privacy guards | Refuses a non-local Ollama address, turns off ChromaDB telemetry, loads nothing from the internet in the UI, binds servers to 127.0.0.1. | `backend/repoguide/config.py`, `backend/tests/test_privacy.py` |
| Tooling | Setup and start scripts for Windows and macOS/Linux, an end-to-end smoke test, 74 backend tests. | `scripts/`, `setup.*`, `start.*`, `backend/tests/` |
| Documentation | README, DECISIONS.md (design choices and limits) and AUDIT_REPORT.md (8 bugs found and fixed on 2026-10-08). | repository root |
| Evaluation data | A 15-file sample shop app with 30 questions + 3 unanswerable; 21 + 3 questions for a real GitHub repo; 11 saved runs. | `eval/` |

For how the parts connect (UI → FastAPI services → SQLite, ChromaDB and the local Ollama models), see the
Architecture diagram in README.md.

## Measured quality

On a real repository the right code is in the top 5 results for 86% of questions, every citation points at
real lines, and all unanswerable questions are refused. Figures are from the last saved runs (2026-10-07),
using `qwen2.5-coder:7b` and `nomic-embed-text` on an RTX 4060 laptop GPU.

| Metric | Sample shop app (30 + 3 questions) | fastapi/full-stack-fastapi-template (21 + 3 questions) |
|---|---|---|
| Right symbol in top 5 results (hit rate @5) | 93% | 86% |
| Ranking quality (MRR @10) | 0.756 | 0.632 |
| Citations pointing at real, current lines | 100% (64 checked) | 100% (41 checked) |
| Answers citing the expected file | 97% | 86% |
| Answers citing the expected symbol | 90% | 81% |
| Unanswerable questions refused | 3 of 3 | 3 of 3 |
| Hit rate @5: vector only / keyword only / hybrid | 93% / 77% / 93% | 76% / 62% / 86% |
| Average answer time | 1.9 s | 2.1 s |

Citation validity checks that a cited line exists, not that it supports the sentence: one real-repo answer
gave a wrong token lifetime (30 minutes instead of 8 days) while citing a real line (issue 1 below).

## Issues

None of the 15 issues stops the app from running. The most important is that an answer can state a detail
the cited code does not support; the two code defects were confirmed on 2026-10-19 and are small fixes.

| # | Issue | Type | Severity | Where | Suggested fix |
|---|---|---|---|---|---|
| 1 | **Answers can state details the cited code does not support.** 1 of 21 real-repo answers said tokens last 30 minutes (real default: 8 days) while citing a real line that holds no value. | Limitation | High | `backend/repoguide/qa/` | Flag numbers, identifiers and file names that appear in none of the cited excerpts; add a "supported claims" metric. |
| 2 | **Search on real repos ranks tests and frontend code above the defining backend code.** All 3 real-repo misses were this. | Limitation | Medium | `backend/repoguide/retrieval/hybrid.py` | Down-weight tests and generated clients unless asked about; boost config files for "default" questions. Grow the real-repo set to about 50 questions first. |
| 3 | **The folder is not a git repository.** There is a `.gitignore` but no history, so the audit's fixes cannot be diffed and nothing can be pushed. | Project hygiene | Medium | repository root | `git init` and commit; the `.gitignore` already excludes `.venv`, `node_modules`, `data/` and `.env`. |
| 4 | **No automated frontend tests.** The UI is covered only by the strict type check; the audit's browser scripts were not kept. | Testing gap | Medium | `frontend/` | Add a few Playwright tests: add a project, ask a question, open a citation. |
| 5 | **Languages other than Python, JavaScript and TypeScript get no symbols or call graph.** Go, Java, C# and Rust files are indexed as 60-line windows. | Limitation | Medium | `backend/repoguide/parsing/` | Add tree-sitter grammars and extractors for the most common languages. |
| 6 | **Creating the same learner twice at once gives a server error.** 8 simultaneous creates of one name failed with `IntegrityError: UNIQUE constraint failed` in 30 of 30 trials; the API returns HTTP 500. Every chat question runs this code. | Code defect | Low | `ensure_learner` in `backend/repoguide/onboarding/service.py` | Use `INSERT ... ON CONFLICT(name) DO NOTHING`, then select the row. |
| 7 | **Finished jobs are never released from memory.** Each job's Future and result stay in `JobManager._futures` for the life of the server. | Code defect | Low | `backend/repoguide/jobs.py` | Remove the entry when `_run` finishes; `wait()` can fall back to the database. |
| 8 | **The macOS/Linux scripts have never been run.** Only Windows was available when they were written. | Testing gap | Low | `setup.sh`, `start.sh` | Run setup and start once on macOS or Linux. |
| 9 | **The test suite is much slower than documented on a first run.** 74 tests took 3 min 4 s on 2026-10-19, not 31 s, because the 7B model loads onto the GPU first. | Project hygiene | Low | `README.md`, `AUDIT_REPORT.md` | Note the cold-start time in the README next to the `-m "not integration"` command. |
| 10 | **Audit and smoke-test data are still in the app's database.** Learners "Audit Learner" and "Smoke Test" own 36 chat messages; there are also 4 feedback records, 1 eval question and 3 quiz attempts. | Project hygiene | Low | `data/repoguide.db` | Start from an empty `data/` folder (or delete the sample project) before a demo or hand-in. |
| 11 | **Test tools and leftovers mixed into the project.** `pytest` and `httpx2` are runtime requirements; `.ruff_cache/` sits at the root though ruff is not a dependency; there is no LICENSE or CI workflow. | Project hygiene | Low | `backend/requirements.txt`, root | Split out a `requirements-dev.txt`, delete `.ruff_cache/`, add a LICENSE and CI if the project will be shared. |
| 12 | **The evaluation sets are small and self-written.** 30 questions on one 15-file app plus 21 on one real repo, written by the system's author. | Limitation | Low | `eval/` | Add questions from users' thumbs-down feedback and a repo in another language. |
| 13 | **Answer scores vary slightly between runs.** GPU nondeterminism moved "cites expected symbol" between 0.90 and 0.93 at temperature 0; retrieval scores never change. | Limitation | Low | evaluation | Compare runs on retrieval metrics, or average answer metrics over several runs. |
| 14 | **The call graph is static.** Dynamic dispatch, inheritance lookups and replacing decorators are not followed, so some callers and callees are missing. | Limitation | Low | `backend/repoguide/graph/resolver.py` | Accept as designed; say so in the code viewer. |
| 15 | **No login or access control.** Anyone using the machine sees every project and learner; servers listen only on 127.0.0.1. | Limitation | Low | whole app | Fine for a single-user local tool; add authentication before exposing it on a network. |

## How this was checked

Every check below was run on 2026-10-19 on the project's own Windows 11 machine; none of the findings come
from the project's documents alone.

| Check | How | Result |
|---|---|---|
| Code read in full | All 52 files in `backend/repoguide/`, the test suite, all 21 frontend source files, scripts and docs | No stubs, TODOs or placeholder functions |
| Backend tests, including real-model tests | `cd backend; ..\.venv\Scripts\python -m pytest` | 74 passed, 0 failed, 0 skipped in 3 min 4 s |
| Frontend type check | `cd frontend; npx tsc -b` (strict, no unused locals or parameters) | 0 errors |
| Local models | `GET http://localhost:11434/api/tags` | `qwen2.5-coder:7b` and `nomic-embed-text` installed |
| Saved data | Read-only query of `data/repoguide.db` | 2 projects ready (15 and 221 files), all 15 tables present |
| Evaluation reports | Read the latest JSON reports in `eval/results/` | Figures match the README and AUDIT_REPORT.md |
| Learner race (issue 6) | Throwaway script on a temporary database: 8 threads, 30 trials | `IntegrityError` in 30 of 30 trials |

Not re-run: the end-to-end smoke test against a running server, the UI in a browser, and a fresh evaluation
run.
