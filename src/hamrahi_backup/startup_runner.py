"""Absolute entry point also works from a source checkout without PYTHONPATH."""
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from hamrahi_backup.cli import main
    raise SystemExit(main())
