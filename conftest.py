"""Repository-wide pytest bootstrap, loaded before any test module imports application code.

``BRAINBENCH_EAGER_IMPORTS=1`` forces every ``lazy import`` to execute eagerly so a missing or
broken deferred dependency fails at import time (CI runs the suite once in each mode). CPython
3.15.0 accepts only ``all`` and ``normal`` for ``PYTHON_LAZY_IMPORTS`` / ``-X lazy_imports``, so
the eager mode is a test-only lazy-imports filter that rejects laziness for every import. On an
interpreter without PEP 810 every import is already eager and this is a no-op.
"""

import os
import sys

if os.environ.get("BRAINBENCH_EAGER_IMPORTS") == "1" and hasattr(sys, "set_lazy_imports_filter"):
    sys.set_lazy_imports_filter(lambda importer, name, fromlist: False)
