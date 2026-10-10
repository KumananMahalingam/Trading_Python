import sys
from pathlib import Path

# Make `ml_service` and `src` importable when pytest runs from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
