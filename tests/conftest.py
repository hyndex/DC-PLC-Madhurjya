import sys
from pathlib import Path

# Ensure project root is on sys.path so "src" package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
