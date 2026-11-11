"""Exercise the real notification window without opening a browser."""
import ctypes
from ctypes import wintypes
import os
import threading
import unittest
from unittest.mock import Mock

from hamrahi_backup.tray import WindowsTray


def interactive_shell():
    if os.name != 'nt':
        return False
    user = ctypes.WinDLL('user32')
    user.GetShellWindow.restype = wintypes.HWND
    return bool(user.GetShellWindow())


@unittest.skipUnless(os.name == 'nt', 'Windows notification area')
class TrayTests(unittest.TestCase):
    @unittest.skipUnless(interactive_shell(), 'Interactive Windows shell required')
    def test_icon_open_explorer_restart_and_cleanup(self):
        opened, exited = threading.Event(), Mock()
        targets = []
        def open_panel(url):
            targets.append(url)
            opened.set()
        tray = WindowsTray('http://127.0.0.1:8765/', exited, open_browser=open_panel)
        user = ctypes.WinDLL('user32')
        user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.IsWindow.argtypes = [wintypes.HWND]
        user.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        try:
            tray.start()
            window = tray.window
            self.assertTrue(user.IsWindow(window))
            user.PostMessageW(window, user.RegisterWindowMessageW('TaskbarCreated'), 0, 0)
            user.PostMessageW(window, 0x800B, 1, 0x202)
            self.assertTrue(opened.wait(3))
            self.assertEqual(targets, ['http://127.0.0.1:8765/'])
            exited.assert_not_called()
        finally:
            tray.close()
        self.assertFalse(tray.thread.is_alive())
        self.assertIsNone(tray.failure)
        self.assertFalse(user.IsWindow(window))

    def test_failure_returns_without_hanging(self):
        tray = WindowsTray('http://127.0.0.1:8765/', Mock())
        def fail():
            raise OSError('synthetic shell error')
        tray._message_loop = fail
        with self.assertRaises(OSError):
            tray.start()
        self.assertFalse(tray.thread.is_alive())
