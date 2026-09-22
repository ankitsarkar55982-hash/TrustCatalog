"""
TrustCatalog source package.

Adds the project root to sys.path so that `import config` works no matter
whether the code is launched from scripts/, dashboard/ or tests/.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

__version__ = "1.0.0"
