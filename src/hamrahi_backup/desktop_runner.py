"""Absolute shortcut target, including source checkouts without installation."""
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from hamrahi_backup.desktop import main
    raise SystemExit(main())
