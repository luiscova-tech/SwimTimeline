#!/usr/bin/env python3
"""Run the local SwimTimeline web app."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webapp.server import main


if __name__ == "__main__":
    main()
