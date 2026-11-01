#!/usr/bin/env python3
"""Convenience entry point: ./venv/bin/python cli.py check input.dxf ..."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dxfqa.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
