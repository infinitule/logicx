"""Make `import mcu` work when pytest runs from kit/mcu (or anywhere else)."""

import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent.parent
if str(KIT) not in sys.path:
    sys.path.insert(0, str(KIT))
