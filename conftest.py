"""Keep `import tastydesk` working even when the editable install goes stale.

uv rebuilds the editable wheel on some invocations and the .pth file can briefly
lose its entry; tests should not fail for a packaging hiccup.
"""

import sys
from pathlib import Path

SRC = Path(__file__).parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
