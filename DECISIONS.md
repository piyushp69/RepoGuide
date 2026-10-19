# Decisions, assumptions and substitutions

Everything below was decided while building RepoGuide without asking questions, as instructed. Each
entry says what was chosen and why. Measurements are from this machine: Windows 11, RTX 4060 Laptop
GPU (8 GB), 24 GB RAM, Ollama 0.40.0.

## Environment and versions

- **Operating system.** Windows 11 was detected, so `setup.ps1` / `start.ps1` are the primary
  entry points. The real logic is in cross-platform Python (`scripts/setup.py`, `scripts/dev.py`);
  `setup.sh` / `start.sh` wrap it for macOS and Linux. The POSIX wrappers were written for portability
  but could only be exercised on Windows here.
- **Python 3.14.3** (the default interpreter) was used for the virtual environment, and every pinned
  dependency installed from wheels. Python 3.10 was also present but is not used: the code needs 3.11+
  (`tomllib`). `setup.ps1` picks the newest of 3.14, 3.13, 3.12 and 3.11.
- **Pinned versions** (verified working together): FastAPI 0.142, Starlette 1.7, Pydantic 2.13,
  ChromaDB 1.5.9, tree-sitter 0.25.2 with python 0.25 / javascript 0.25 / typescript 0.23 grammars,
  rank-bm25 0.2.2, pathspec 1.1. Frontend: React 19.3, Vite 8.3, Tailwind 4.3, TypeScript 7.0,
  TanStack Query 5, React Router 7, Mermaid 12.1 (`npm ci` installs exactly `package-lock.json`).

### Substitutions and additions to the requested stack

No requested library was replaced. These version pins and additions were needed:

1. **tree-sitter pinned to 0.25.2 instead of the latest 0.26.0.** With 0.26.0 on Python 3.14/Windows the
   interpreter crashed with an access violation during garbage collection after walking syntax trees
   (reproduced in isolation: parse, call `child_by_field_name` / `prev_named_sibling` on top-level
   nodes, then `gc.collect()`). 0.25.2 has a cp314 wheel and passes the same reproduction, the full
   test suite, and a stress run over the whole CPython standard library (2,284 files).
2. **`snowballstemmer`** added for BM25 tokenisation, so that "discounts applied" matches `apply_discount`.
   It is pure Python, with no network or model downloads.
3. **`httpx2`** added as a test dependency. Starlette 1.7's `TestClient` prefers it and warns otherwise.
   The application itself uses `httpx`.
4. **npm `overrides` pins KaTeX to `^0.19.0`.** Mermaid 12 depends on a KaTeX version with a
   prototype-pollution advisory (GHSA-238p-pmpm-9mq7); `npm audit fix` would have downgraded Mermaid
   to 10.x. KaTeX is only used for math in diagrams, which RepoGuide never generates. `npm audit` now
   reports 0 vulnerabilities.
5. **`lucide-react` 1.x has no brand icons** (no GitHub logo), so GitHub projects use a generic git-branch icon.

## Models

- **Chat: `qwen2.5-coder:7b`; embeddings: `nomic-embed-text`** (the requested defaults). Neither was
  installed, so both were pulled. `llama3:latest` was already installed, but it is not
  code-specialised, so it was not used. Measured with `ollama ps`: qwen2.5-coder:7b takes 5.0 GB at
  an 8,192-token context and nomic-embed-text 0.3 GB, both 100% on GPU. Both names live in `.env`.
- **Context window 8,192 tokens** (`LLM_NUM_CTX`). That fits the 7B model, the embedding model and
  the KV cache in 8 GB of VRAM. The answer context is capped at about 16,000 characters of code
  (`CONTEXT_CHAR_BUDGET`, roughly 4-5k tokens) to leave room for instructions, history and the answer.
- **Embedding task prefixes.** nomic-embed-text was trained with `search_document:` / `search_query:`
  prefixes, which RepoGuide adds automatically (`EMBED_*_PREFIX=auto`; mxbai-embed-large is also
  recognised; other models get no prefix unless configured).
- **Answer decoding.** Answers use temperature 0 (`QA_TEMPERATURE`), an "evidence-first" instruction,
  and a 1,024-token cap (`QA_MAX_TOKENS`). In side-by-side runs, temperature 0.1 once produced a
  confident but wrong answer, while temperature 0 with the evidence-first instruction cited correctly
  on every test question. The cap was added after one evaluation run hit a degenerate repetition:
  Ollama kept shifting the context window (its log showed `slot context shift`) until the 10-minute
  timeout. Answers that stop at the cap get a visible "cut off" note. The request timeout is now 300 s.
- **Other generation** (overview, learning paths, quizzes) uses temperature 0.1 and Ollama's JSON-schema
  structured outputs, with 2,048-token caps and retries on invalid JSON.

## Privacy and security

- **Loopback only.** `OLLAMA_BASE_URL` is validated: a non-loopback host refuses to start. The backend
  and Vite bind to `127.0.0.1`.
- **No telemetry.** ChromaDB's anonymized telemetry is disabled (`ANONYMIZED_TELEMETRY=False` before
  import, and `Settings(anonymized_telemetry=False)`; in 1.5.9 its capture is already a no-op). Chroma's
  default ONNX embedding function, which would download a model, is never used: collections are created
  with `embedding_function=None` and always receive vectors from Ollama.
- **The UI makes no external requests**: no web fonts (system font stack), Mermaid and Prism are bundled.
- **Secrets are not indexed.** `.env`, `.env.*` (except `.example`/`.sample`/`.template`), private keys
  and certificates (`*.pem`, `*.key`, `id_rsa`, …) are skipped even when not git-ignored. Code is never
  sent anywhere, but a secret in the index could be quoted in answers.
- **File reads are restricted** to files in the project's index and inside the project root, so there is
  no path traversal through the file viewer. Evaluation report IDs are checked the same way.
- **Git** runs with `GIT_TERMINAL_PROMPT=0` (never hangs asking for credentials), as an argument list
  (no shell), shallow (`--depth 1`). Only `github.com` URLs are accepted, as the spec asked.
  Deleting a GitHub project removes its clone; deleting a local project never touches the folder.

## Ingestion

- `.gitignore` handling uses `pathspec`'s `GitIgnoreSpec` for every nested `.gitignore`; deeper files
  override shallower ones, and an ignored directory is pruned whole (as in git).
- **Skipped by default** (counted per reason and shown in the UI): dependency and build folders
  (`node_modules`, `dist`, `build`, `out`, `target`, `vendor`, `coverage`, caches, IDE folders, …),
  any folder containing `pyvenv.cfg` or `conda-meta` (virtual environments under any name), lock files,
  binaries (by extension, then by a NUL byte in the first 8 KB), minified/generated files (`*.min.js`,
  source maps, `_pb2.py`, a first line over 8 KB), data and log dumps (`.log`, `.csv`, `.jsonl`), empty
  files, symlinks, files over 512 KB, and anything past 5,000 files. Folder names like `build` or `out`
  are occasionally real source; that trade-off is configurable only through `.gitignore` negation in
  this version.
- Text is read as UTF-8 with replacement, with the BOM stripped and line endings normalised, so line
  numbers match what editors show.
- Several projects can be indexed; the UI switches between them, and each has its own Chroma collection.

## Parsing and chunking

- **Granularity.** Functions and methods are the unit. A class of up to 60 lines (`CLASS_INLINE_MAX_LINES`)
  stays one chunk; longer classes become a header chunk (signature, docstring, class attributes) plus
  one chunk per method named `Class.method`. Leading comments, JSDoc and decorators stay with their
  definition; a comment on line 1 is treated as a file header, not as a docstring. Top-level code
  becomes "module" chunks. Definitions longer than 120 lines are split, preferring blank lines.
- **JS/TS forms covered:** function declarations, `const f = () => …`, HOC-wrapped components
  (`React.memo(() => …)`), classes (incl. TS abstract and decorated classes), class field arrow
  functions, interfaces, type aliases, enums, namespaces, `declare` statements, `export default`,
  `module.exports.x = function`, `Foo.prototype.bar = function`. JSX usages of capitalised
  components count as calls.
- **Other text files**: Markdown is split by heading (ignoring `#` inside code fences, with tiny sections
  merged into the next); everything else uses 60-line windows with 10 lines of overlap.
- `.tsx` files report language `typescript`; the grammar is chosen by extension.
- A file whose tree-sitter extraction fails falls back to line windows (logged) instead of failing the index.

## Code graph

- Python module names are registered relative to the repository root and to every ancestor folder that
  is not a package, which handles `src/` layouts and apps run from a subfolder (`from services.orders
  import …` in the sample app). An import whose first part is a standard-library module only resolves to
  a sibling file, so `import json` never links to an unrelated `json.py`.
- JS/TS resolution: relative paths with extension and `index.*` probing, `.js` imports of `.ts` files,
  `tsconfig`/`jsconfig` `baseUrl` + `paths` (JSONC parsed), the common `@/` → `src/` convention, and
  barrel re-exports up to 4 levels.
- Call resolution is static and best-effort, with a confidence score: local, imported, `self`/`this`,
  module-alias and `Class.method` calls get 0.9-1.0; otherwise a name unique in the project gets 0.5 (0.7
  if the caller's file imports the candidate). Very generic names (`get`, `save`, `render`, …) are
  never linked heuristically. Only the most confident edge is kept per caller/callee chunk pair.
- The whole graph is rebuilt on each index run. It is cheap (5.5 s for 85k call edges on the CPython
  stdlib) and avoids stale edges.

## Indexing

- **Incremental by content hash** (SHA-256 of the file bytes). Chunk IDs are content-addressed
  (project, path, lines, symbol, content hash), so unchanged chunks of an edited file keep their
  vectors. A test checks that appending one function re-embeds exactly one chunk.
- Jobs (indexing, overview, onboarding, quizzes, evaluation) run in an in-process thread pool (2
  workers) and record phase/current/total in SQLite. The UI polls them. On restart, interrupted jobs
  are marked as failed instead of hanging forever, and a project whose indexing was interrupted is set
  to `error` with a message to re-index (it used to show "indexing" forever). Only the server does this
  cleanup at startup: `evaluate.py` running in its own process opens the same database and must not
  fail the server's live jobs.
- After indexing, the overview is regenerated when anything changed or none exists. If overview
  generation fails, the index still counts as successful and the failure is shown as a warning.

## Retrieval (and what the evaluation showed)

- **Hybrid** = vector search (25 candidates) + BM25 (25) + direct symbol/file-name lookup, merged with
  reciprocal-rank fusion (k = 60) and deduplicated; top 8 seeds; graph expansion adds up to 2 neighbours
  per seed, 6 in total.
- **BM25 is weighted 0.5 in the fusion.** The first real-model run with equal weights showed hybrid
  *below* vector-only (hit@5 0.885 vs 0.923, MRR 0.704 vs 0.831) because BM25 is weaker on paraphrased
  questions. Offline sweeps over weights and k picked 0.5 as a middle ground: hit@5 matched vector-only,
  MRR improved, and BM25 still contributes. Hybrid search was required by the spec and helps on
  exact-string questions. To cover those honestly, 4 exact-term questions (q27-q30: an env var, an
  error message, a table name) were **added to the evaluation set after that first run**. On those,
  BM25 lifted the right chunk (e.g. q27 vector rank 5 → hybrid 3, q29 5 → 2). Pure vector search still
  has a higher MRR on the full set (0.800 vs 0.756); this is reported in every run's ablation table.
- **Each retriever's best hit is guaranteed a seed slot.** Rank fusion rewards agreement, so a chunk
  ranked first by one retriever and missed by the other could sink below mediocre "both lists" hits.
- **Query focusing.** Indexing RepoGuide itself showed that the project's own name ("How does
  RepoGuide decide which files to skip…") dragged BM25 toward docs mentioning the name and vector search
  toward `projects.py`: the right file ranked 16th and 24th. The name (also the repo or folder name)
  and unambiguous self-references ("this project", "the codebase") are now removed from the search
  query. Ambiguous phrases such as "the project" are kept because they can be domain terms. After
  this, the right file ranked 1st.

## Question answering

- Context excerpts are numbered `[n]` with path, line range, symbol and graph relation; the model must
  cite `[n]`; citations are mapped back to exact file and line ranges (trimmed if an excerpt was cut to
  fit the budget). Brackets inside code (`items[0]`) are ignored.
- **"Not found" contract:** the model must reply "I could not find this in the indexed code."; that phrase
  (and close variants) marks the answer as not found (`grounded = 0`) and the UI labels it.
- **Self-check:** a draft with no valid citation (and not a not-found reply) is regenerated once with a
  stricter instruction, without showing the unsupported draft again (showing it anchored the model to
  its wrong claim in testing). If it still cites nothing, the answer is stored as *unverified*
  (`grounded = -1`) with a warning. Mentioned file paths that are not in the index are listed as
  warnings.
- **Citation alignment.** The 7B model sometimes cites the neighbouring excerpt: in the smoke test it
  described `placeOrder` correctly but cited `[4]`, the `cancelOrder` excerpt next to it. A marker is
  re-pointed only when its sentence names an identifier in backticks, the cited excerpt does not
  contain that identifier anywhere, and exactly one other excerpt defines it. Code blocks are never
  changed, and every correction is shown under the answer ("Citation [4] was changed to [5]…").
  The evaluation reports how often this happens and a stricter metric, "answers citing the expected
  symbol", which this case would have failed.
- Chat history is per project and per learner (guests share one history). The last 2 exchanges are
  sent to the model, and short or pronoun-based follow-ups are retrieved together with the previous
  question.

## Overview

- Facts come from static analysis: manifests (package.json, requirements, pyproject, Pipfile, setup.py,
  go.mod, Cargo.toml, Gemfile, composer.json, pom.xml), imported external packages, a known-package
  table for display names and categories, and entry-point heuristics (`__main__` guards, app factories
  such as `Flask(__name__)` / `FastAPI(`, `createRoot(`, `.listen(`, package.json `main`/scripts).
  Folders become modules (depth chosen to keep about 24 or fewer).
- The model writes the summary and module descriptions in one JSON-schema call whose module `path`
  field is an `enum` of the real paths. Without it, the 7B model returned `backend/services/` and file
  names, which matched nothing. The execution flow is a second call over call chains traced from the
  entry points. File paths the model returns are validated against the index.
- **The model is not told the project name.** It is a label the user typed, and with little code to go
  on the 7B model built the summary around it (the audit found a 7-line Go "hello" program registered as
  "Audit no supported languages" described as "an audit tool that checks for unsupported languages").
  A real name still reaches the model through the README. A project with no indexed files gets a fixed
  factual summary without any model call.
- If the model is unavailable when the overview is regenerated, the previous summary, module
  descriptions and flow are kept and a warning says they may be out of date; the static parts are
  rebuilt as usual.
- **The Mermaid diagram is built deterministically** (module nodes, import edges, HTTP edges detected
  from `fetch`/`axios` calls to `/api` routes, top frameworks), so it always renders. Light-theme node
  styles are swapped for dark-theme ones in the browser.

## Onboarding, quizzes, progress

- Roles: frontend, backend, data/ML, full-stack. File relevance is scored from folder names, file types,
  imported frameworks (React, Flask, pandas, …), how many files import a file, and entry points; README
  files get a bonus and tests a penalty. When fewer than 3 files clearly match a role (for example
  data/ML in the sample shop), the path is labelled "limited fit" instead of inventing ML code.
- Quizzes: 3 questions per step, 4 options each. A question is kept only if its evidence line is found
  in one of the step's files, which also gives the exact line to show after submission. Options are
  shuffled deterministically (the model tends to put the answer first). 60% or more completes the step
  automatically; steps can also be ticked by hand.
- Regenerating a path creates a new version; progress on older versions stays on the manager dashboard.
- **No authentication** (as specified): a learner name picker (names are stored in SQLite, the current
  choice in the browser) and a "Manager view" toggle that only reveals the dashboard. Anyone with access
  to the machine can see everything; the server only listens on localhost.
- "Questions learners asked most" groups questions by normalised text (lower case, no punctuation).
  It does not merge paraphrases.

## Feedback and export

- One feedback record per answer (changing the rating updates it). Each record stores the question,
  answer, retrieved chunk IDs, cited chunk IDs, rating, comment, learner and timestamps.
- "Add to evaluation set" requires a file that exists in the index (symbol optional, validated); the
  questions form a per-project evaluation set that the harness and UI can run.
- **Training pairs** (`backend/scripts/export_feedback.py`, also a download button): for thumbs-down
  answers curated into the eval set, the positive is the chunk of the marked symbol (or the
  best-BM25 chunk of the marked file) and the negatives are retrieved chunks from other files; for
  thumbs-up answers each cited chunk is a positive and retrieved but uncited chunks are hard negatives.
  Thumbs-down answers without a correction carry no positive and are skipped. No training is done.

## Evaluation

- **Fixture**: `eval/sample_repo` ("ShopLite"), 15 files: Flask + SQLite backend (app factory, config,
  db, models, auth, routes, 4 service modules), a React/TS frontend (API client, cart, checkout form),
  README and requirements. It is a fixture only and is never executed.
- **Question set**: 30 questions with expected file and symbol + 3 unanswerable questions. A test
  verifies that every expected file and symbol exists.
- **Relevance** = the chunk is in the expected file and its line range overlaps the expected symbol's
  definition (taken from the index), so a whole-class chunk counts for one of its methods. File-level
  numbers are reported alongside.
- Citation validity checks files on disk, line bounds, and that the cited lines still equal the indexed
  text. Inline path mentions and non-existent `[n]` markers are included, so the metric can fail;
  a test feeds deliberately bad citations to prove it.
- Answer generation in the harness uses the same code path as the chat (same prompt, self-check and
  limits) but without history and without saving to chat.
- Reports are written as JSON + Markdown to `eval/results/`; the UI reads the history from there.

### Results of the final verification run (`eval/results/20261007-212439-shoplite-sample.*`)

Run after a from-scratch reinstall, through the running backend's API:

| Metric | Value |
|---|---|
| Hit rate @5, expected symbol / expected file | 0.933 / 1.000 |
| MRR @10, expected symbol / expected file | 0.756 / 0.889 |
| Relevant chunk in the answer context | 1.000 |
| Citation validity (65 citations) | 1.000 |
| Answers with at least one citation | 1.000 |
| Answers citing the expected file / the expected symbol | 0.967 / 0.933 |
| Drafts that needed the citation rewrite | 0.000 |
| Answers with a citation number corrected | 0.033 (1 of 30) |
| Abstention on the 3 unanswerable questions | 1.000 |
| Average answer time | 1.74 s |
| Ablation hit@5 / MRR: vector, BM25, hybrid | 0.933 / 0.800, 0.767 / 0.689, 0.933 / 0.756 |

The two retrieval misses at symbol level (q13 "walk me through placing an order", rank 7; q21 "which
endpoint places an order", rank 6) still have the right file in the top 5 (ranks 1 and 3) and the right
chunk in the answer context. Two answers did not cite the expected symbol: q04 (free shipping) cited
the pricing module's constants instead of `shipping_cost`, and q12 (cancelling an order) cited
`release_stock` in `inventory.py` rather than `OrderService.cancel_order`. Both were reasonable
answers, but the strict metric counts them as misses. q13's single citation correction is the one
counted above.

Earlier runs are kept in `eval/results/` and on the Evaluation page: the first run (26 questions,
equal fusion weights, temperature 0.1) scored hit@5 0.885, MRR 0.704, 92% of answers cited something
and 88.5% cited the expected file.

## Known limitations

- A 7B model can still state something wrong while citing the right code. Citation validity proves a
  citation points at real, current lines, not that every sentence is correct. The evidence-first
  prompt, temperature 0, the citation self-check and the unverified/warning labels reduce this but do
  not eliminate it.
- The call graph is static: no dynamic dispatch, no inheritance-based method lookup, no
  decorator/metaclass magic, no cross-language links (HTTP links are only drawn in the diagram).
- Tree-sitter chunking covers Python, JavaScript and TypeScript only; other languages (Go, Java, …) are
  indexed with line windows. Notebooks are indexed as raw JSON.
- The evaluation set is small (30 questions on one 15-file app) and was written by the same author as
  the system, so its numbers are indicative, not a benchmark.
- Large repositories work but take time to embed (5,000-file cap by default). The overview and module
  map are tuned for small and medium projects.
- GitHub: public repositories only (no credential prompts), default or specified branch, shallow clone.
- No authentication or access control: it is a single-machine tool by design.
- macOS/Linux scripts were written but not run in this session (only Windows was available).
