"""Pytest configuration for reconciled framework tests."""
import sys
from pathlib import Path

# Add scripts directory to path
scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)
