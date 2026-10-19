"""Start the RepoGuide API server: ``python -m repoguide`` (run from the ``backend`` folder)."""

import logging

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("repoguide.api.app:app", host=settings.backend_host, port=settings.backend_port, log_level="info")


if __name__ == "__main__":
    main()
