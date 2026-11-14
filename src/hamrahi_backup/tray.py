"""Windows notification icon owned by the background server process."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import threading
import time
import webbrowser


class WindowsTray:
    def __init__(self, url, exit_callback, open_browser=webbrowser.open):
        self.url = url
        self.exit_callback = exit_callback
        self.open_browser = open_browser
        self.ready = threading.Event()
        self.window = None
        self.failure = None
        self.last_open = 0
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='windows-tray')

    def start(self):
        if os.name != 'nt':
            raise OSError('Windows notification area required')
        self.thread.start()
        if not self.ready.wait(5) or self.failure or not self.window:
            self.close()
            raise OSError('Cannot create notification icon')

    def close(self):
        self.stopping.set()
        if self.window:
            user = ctypes.WinDLL('user32')
            user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user.PostMessageW(self.window, 0x10, 0, 0)
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)

    def _dispatch(self, callback):
        def invoke():
            try:
                callback()
            except Exception:
                pass
        threading.Thread(target=invoke, daemon=True, name='tray-action').start()

    def _run(self):
        try:
            self._message_loop()
        except Exception as error:
            self.failure = type(error).__name__
        finally:
            self.window = None
            self.ready.set()

    def _message_loop(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        pointer = ctypes.c_void_p
        result_type = ctypes.c_ssize_t
        callback_type = ctypes.WINFUNCTYPE(result_type, wintypes.HWND, wintypes.UINT,
                                           wintypes.WPARAM, wintypes.LPARAM)

        class WindowClass(ctypes.Structure):
            _fields_ = [('style', wintypes.UINT), ('callback', callback_type),
                        ('class_extra', ctypes.c_int), ('window_extra', ctypes.c_int),
                        ('instance', pointer), ('icon', pointer), ('cursor', pointer),
                        ('background', pointer), ('menu', wintypes.LPCWSTR),
                        ('class_name', wintypes.LPCWSTR)]

        class IconData(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('window', wintypes.HWND),
                        ('identifier', wintypes.UINT), ('flags', wintypes.UINT),
                        ('message', wintypes.UINT), ('icon', pointer),
                        ('tip', wintypes.WCHAR * 128), ('state', wintypes.DWORD),
                        ('state_mask', wintypes.DWORD), ('info', wintypes.WCHAR * 256),
                        ('version', wintypes.UINT), ('info_title', wintypes.WCHAR * 64),
                        ('info_flags', wintypes.DWORD), ('guid', ctypes.c_ubyte * 16),
                        ('balloon_icon', pointer)]

        kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel.GetModuleHandleW.restype = pointer
        user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
        user.UnregisterClassW.argtypes = [wintypes.LPCWSTR, pointer]
        user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            pointer, pointer, pointer, pointer]
        user.CreateWindowExW.restype = wintypes.HWND
        user.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.DefWindowProcW.restype = result_type
        user.LoadIconW.argtypes = [pointer, pointer]
        user.LoadIconW.restype = pointer
        user.LoadImageW.argtypes = [pointer, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user.LoadImageW.restype = pointer
        user.DestroyIcon.argtypes = [pointer]
        user.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        user.DestroyWindow.argtypes = [wintypes.HWND]
        user.IsWindow.argtypes = [wintypes.HWND]
        user.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.CreatePopupMenu.restype = pointer
        user.AppendMenuW.argtypes = [pointer, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        user.DestroyMenu.argtypes = [pointer]
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        user.TrackPopupMenu.argtypes = [pointer, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, wintypes.HWND, pointer]
        shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(IconData)]
        user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user.DispatchMessageW.restype = result_type
        callback_message = 0x800B
        taskbar_created = user.RegisterWindowMessageW('TaskbarCreated')
        branded_icon = user.LoadImageW(None, str(Path(__file__).with_name('web') / 'app.ico'), 1, 0, 0, 0x50)
        if not branded_icon:
            raise OSError('Application notification icon could not be loaded')
        icon = IconData(size=ctypes.sizeof(IconData), identifier=1, flags=7, version=3,
                        message=callback_message, icon=branded_icon,
                        tip='Abre Hamrahi API - click to open')

        def window_proc(window, message, first, second):
            if taskbar_created and message == taskbar_created:
                shell.Shell_NotifyIconW(0, ctypes.byref(icon))
                shell.Shell_NotifyIconW(4, ctypes.byref(icon))
                return 0
            if message == callback_message:
                if second == 0x202:
                    if time.monotonic() - self.last_open > .75:
                        self.last_open = time.monotonic()
                        self._dispatch(lambda: self.open_browser(self.url))
                elif second in (0x205, 0x7B):
                    menu = user.CreatePopupMenu()
                    try:
                        user.AppendMenuW(menu, 0, 1, 'بازکردن ابر همراهی')
                        user.AppendMenuW(menu, 0x800, 0, None)
                        user.AppendMenuW(menu, 0, 2, 'خروج و توقف همگام‌سازی')
                        point = wintypes.POINT()
                        user.GetCursorPos(ctypes.byref(point))
                        user.SetForegroundWindow(window)
                        selected = user.TrackPopupMenu(menu, 0x102, point.x, point.y, 0, window, None)
                        user.PostMessageW(window, 0, 0, 0)
                        if selected == 1:
                            self._dispatch(lambda: self.open_browser(self.url))
                        elif selected == 2:
                            self._dispatch(self.exit_callback)
                    finally:
                        user.DestroyMenu(menu)
                return 0
            if message == 0x10:
                user.DestroyWindow(window)
                return 0
            if message == 2:
                shell.Shell_NotifyIconW(2, ctypes.byref(icon))
                user.PostQuitMessage(0)
                return 0
            return user.DefWindowProcW(window, message, first, second)

        callback = callback_type(window_proc)
        instance = kernel.GetModuleHandleW(None)
        class_name = 'AbreHamrahiAPI.Tray.' + str(os.getpid())
        window_class = WindowClass(callback=callback, instance=instance, class_name=class_name, icon=branded_icon)
        if not user.RegisterClassW(ctypes.byref(window_class)):
            user.DestroyIcon(branded_icon)
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.window = user.CreateWindowExW(0, class_name, class_name, 0, 0, 0, 0, 0,
                                                None, None, instance, None)
            if not self.window:
                raise ctypes.WinError(ctypes.get_last_error())
            icon.window = self.window
            if self.stopping.is_set():
                return
            if not shell.Shell_NotifyIconW(0, ctypes.byref(icon)):
                raise OSError('Shell notification icon refused')
            shell.Shell_NotifyIconW(4, ctypes.byref(icon))
            self.ready.set()
            message = wintypes.MSG()
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user.TranslateMessage(ctypes.byref(message))
                user.DispatchMessageW(ctypes.byref(message))
        finally:
            if self.window and user.IsWindow(self.window):
                user.DestroyWindow(self.window)
            user.UnregisterClassW(class_name, instance)
            user.DestroyIcon(branded_icon)
