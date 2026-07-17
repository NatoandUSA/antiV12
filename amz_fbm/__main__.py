#!/usr/bin/env python3
"""Enable ``python -m amz_fbm ...`` (used by shortcuts, wrappers, and autostart)."""
import sys

from amz_fbm.cli import main

if __name__ == "__main__":
    sys.exit(main())
