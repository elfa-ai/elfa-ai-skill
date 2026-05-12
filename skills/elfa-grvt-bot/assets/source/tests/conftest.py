import os
import sys
from pathlib import Path

# Ensure src/ is on the import path for tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
