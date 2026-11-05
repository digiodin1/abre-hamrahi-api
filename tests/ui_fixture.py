"""Manual browser QA fixture. No real account, provider requests, or startup writes."""
from pathlib import Path
import tempfile
import time

from hamrahi_backup.engine import BackupManager, BackupError
from hamrahi_backup.server import PanelServer
from test_engine import FakeCloud


def main():
    qa = Path(__file__).resolve().parent.parent / '.qa'
    qa.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=qa) as temporary:
        base = Path(temporary)
        source = base / 'نمونه پوشه'
        source.mkdir()
        for i in range(105):
            (source / f'{i:03}.txt').write_text('synthetic test data', encoding='utf-8')
        manager = BackupManager(base, FakeCloud(), port=8801)
        manager.startup.status = lambda: {'supported': False, 'registered': False, 'error': None}
        def no_startup(enabled):
            raise OSError('Startup changes are disabled in the synthetic QA fixture')
        manager.startup.configure = no_startup
        manager.save_config(manager.config | {'settleSeconds': 0, 'folders': [{'path': str(source)}]})
        snapshot = manager.snapshot
        fail_once = True
        def read(path, target):
            nonlocal fail_once
            time.sleep(0.025)
            if path.name == '000.txt' and fail_once:
                fail_once = False
                raise BackupError('خطای ساختگی برای آزمون تلاش مجدد')
            return snapshot(path, target)
        manager.snapshot = read
        manager.sync(preview=True)
        server = PanelServer(manager, 8801)
        print('SYNTHETIC_QA_READY http://127.0.0.1:8801', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            manager.close()


if __name__ == '__main__':
    main()
