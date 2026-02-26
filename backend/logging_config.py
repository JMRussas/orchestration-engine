#  Orchestration Engine - Logging Configuration
#
#  Configures structured logging for the orchestration engine.
#
#  Depends on: (none)
#  Used by:    run.py, app.py

import logging
import sys


def setup_logging(level: str = "INFO"):
    """Configure structured logging for the orchestration engine."""
    root = logging.getLogger("orchestration")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
        root.addHandler(handler)

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
