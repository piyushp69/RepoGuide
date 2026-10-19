"""Run the RepoGuide evaluation harness.

Examples (from the repository root):
    .venv/Scripts/python backend/scripts/evaluate.py                # sample repo, with answer generation
    .venv/Scripts/python backend/scripts/evaluate.py --no-llm       # retrieval metrics only (fast)
    .venv/Scripts/python backend/scripts/evaluate.py --project-id 3 # a project's feedback-derived set
    .venv/Scripts/python backend/scripts/evaluate.py --project-id 3 --eval-set my_questions.json

Results are written to eval/results/<run-id>.json and .md (plus latest.json / latest.md) and
appear on the Evaluation page. If the RepoGuide backend is running, the run is started
through its API so that only one process writes to the vector store.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from repoguide.config import get_settings  # noqa: E402

METRIC_LABELS = [
    ("hit_at_5", "Hit rate @5 (expected symbol)"),
    ("mrr", "MRR @10 (expected symbol)"),
    ("file_hit_at_5", "Hit rate @5 (expected file)"),
    ("file_mrr", "MRR @10 (expected file)"),
    ("in_context_rate", "Relevant chunk in answer context"),
    ("citation_validity", "Citation validity"),
    ("citations_checked", "Citations checked"),
    ("answers_with_citations", "Answers with >= 1 citation"),
    ("answers_citing_expected_file", "Answers citing the expected file"),
    ("answers_citing_expected_symbol", "Answers citing the expected symbol"),
    ("answers_rewritten", "Drafts rewritten (cited nothing)"),
    ("answers_with_corrected_citations", "Answers with a corrected citation"),
    ("abstention_rate", "Abstention on unanswerable questions"),
    ("avg_answer_seconds", "Average answer time (s)"),
]


def print_report(report: dict) -> None:
    metrics = report["metrics"]
    print(f"\nEvaluation {report['run_id']} - {report['eval_set']['name']} "
          f"({metrics['questions']} questions, {report['duration_seconds']} s)")
    for key, label in METRIC_LABELS:
        if key in metrics:
            value = metrics[key]
            print(f"  {label:<40} {value:.3f}" if isinstance(value, float) else f"  {label:<40} {value}")
    print("  Retrieval ablation (hit@5 / MRR):")
    for mode, values in report["ablation"].items():
        print(f"    {mode:<8} {values['hit_at_5']:.3f} / {values['mrr']:.3f}")
    files = report.get("files") or {}
    if files:
        print(f"\nSaved {files.get('json')}\n      {files.get('markdown')}")


def run_via_api(base: str, args: argparse.Namespace) -> int:
    body = {"source": "project" if args.project_id else "sample", "project_id": args.project_id,
            "with_llm": not args.no_llm, "limit": args.limit}
    with httpx.Client(base_url=base, timeout=30) as client:
        response = client.post("/api/eval/run", json=body)
        if response.status_code != 200:
            print(f"Backend refused the run: {response.text}", file=sys.stderr)
            return 1
        job = response.json()
        print(f"Started evaluation job {job['id']} on the running backend ({base}).")
        last = None
        while job["status"] not in ("done", "error"):
            time.sleep(2)
            job = client.get(f"/api/jobs/{job['id']}").json()
            status = (job.get("phase"), job.get("current"), job.get("total"))
            if status != last:
                print(f"  [{job.get('phase')}] {job.get('current')}/{job.get('total')} {job.get('message') or ''}")
                last = status
        if job["status"] == "error":
            print(f"Evaluation failed: {job['error']}", file=sys.stderr)
            return 1
        report = client.get(f"/api/eval/runs/{job['result']['run_id']}").json()
        results_dir = get_settings().eval_results_dir
        report["files"] = {"json": str(results_dir / f"{report['run_id']}.json"),
                           "markdown": str(results_dir / f"{report['run_id']}.md")}
        print_report(report)
    return 0


def run_in_process(args: argparse.Namespace) -> int:
    from repoguide.jobs import PrintReporter
    from repoguide.services import build_services

    services = build_services(job_workers=1)
    try:
        if not args.no_llm or not args.project_id:
            health = services.ollama.health(services.settings.chat_model, services.settings.embed_model)
            if not health["reachable"]:
                print(f"Ollama is not reachable: {health['error']}", file=sys.stderr)
                return 2
            missing = [m for m, ok in ((services.settings.embed_model, health["embed_model_available"]),
                                       (services.settings.chat_model, health["chat_model_available"])) if not ok]
            if missing:
                print(f"Missing Ollama model(s): {', '.join(missing)}. Run `ollama pull <model>`.", file=sys.stderr)
                return 2
        reporter = PrintReporter()
        if args.project_id:
            eval_set = (services.evaluation.load_sample_set(args.eval_set) if args.eval_set
                        else services.evaluation.custom_set(args.project_id))
            project_id = args.project_id
        else:
            print("Indexing the sample repository (incremental)...")
            project_id = services.evaluation.sample_project(reporter)
            eval_set = services.evaluation.load_sample_set(args.eval_set)
        report = services.evaluation.run(eval_set, project_id, with_llm=not args.no_llm, limit=args.limit,
                                         reporter=reporter)
        print_report(report)
        if args.json:
            print(json.dumps(report["metrics"], indent=2))
        return 0
    finally:
        services.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eval-set", type=Path, help="evaluation set JSON (default: eval/eval_set.json); "
                        "with --project-id it is run against that project instead of the sample repo")
    parser.add_argument("--project-id", type=int, help="evaluate a project's feedback-derived question set")
    parser.add_argument("--no-llm", action="store_true", help="skip answer generation (retrieval metrics only)")
    parser.add_argument("--limit", type=int, help="only evaluate the first N questions")
    parser.add_argument("--in-process", action="store_true", help="never use a running backend")
    parser.add_argument("--json", action="store_true", help="also print the metrics as JSON")
    args = parser.parse_args()

    settings = get_settings()
    base = f"http://{settings.backend_host}:{settings.backend_port}"
    if not args.in_process and not args.eval_set:
        try:
            httpx.get(f"{base}/api/ping", timeout=10)
        except httpx.ConnectError:
            return run_in_process(args)  # nothing is listening: evaluate in this process
        except httpx.HTTPError:
            pass  # something is listening but slow: still go through it rather than share the vector store
        return run_via_api(base, args)
    return run_in_process(args)


if __name__ == "__main__":
    sys.exit(main())
