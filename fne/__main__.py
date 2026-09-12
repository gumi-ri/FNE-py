"""Entry point for ``python -m fne``.

Delegates to ``fne.launcher`` so the module route, the console script and the
frozen executable all share one decision about CLI vs GUI.
"""

import sys

from fne.launcher import main

if __name__ == "__main__":
    sys.exit(main())
