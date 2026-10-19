"""Export answer feedback as JSONL training pairs for a future reranker (no training is done).

Each line is {"query", "positive", "negatives", "label_source", ...}:
- thumbs-down answers that were added to the evaluation set use the chunk of the file
  (and symbol) the user marked as correct as the positive, and the retrieved chunks from
  other files as negatives ("thumbs_down_corrected");
- thumbs-up answers use each cited chunk as a positive and the retrieved but uncited
  chunks as hard negatives ("thumbs_up_cited").

Examples (from the repository root):
    .venv/Scripts/python backend/scripts/export_feedback.py
    .venv/Scripts/python backend/scripts/export_feedback.py --project-id 2 --out feedback.jsonl
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repoguide.config import get_settings  # noqa: E402
from repoguide.db import Database  # noqa: E402
from repoguide.feedback.service import FeedbackService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-id", type=int, help="only export feedback for this project")
    parser.add_argument("--out", type=Path, help="output file (default: data/exports/feedback-pairs-<time>.jsonl)")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.db_path.exists():
        print(f"No RepoGuide database at {settings.db_path}; nothing to export.", file=sys.stderr)
        return 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = args.out or settings.data_dir / "exports" / f"feedback-pairs-{stamp}.jsonl"
    service = FeedbackService(SimpleNamespace(db=Database(settings.db_path)))  # SQLite only
    summary = service.export_jsonl(out.resolve(), args.project_id)
    print(f"Wrote {summary['pairs']} training pairs from {summary['feedback_records']} feedback records "
          f"to {summary['path']}")
    for source, count in sorted(summary["by_source"].items()):
        print(f"  {source}: {count}")
    if summary["pairs"] == 0:
        print("No usable pairs yet: give thumbs up to cited answers, or add thumbs-down questions to the "
              "evaluation set with the correct file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
