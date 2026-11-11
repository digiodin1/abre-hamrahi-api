"""Open the native folder dialog in a dedicated GUI process."""
import json
import sys
import tkinter
from tkinter import filedialog


def choose_folder():
    root = tkinter.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        path = filedialog.askdirectory(parent=root, title='Abre Hamrahi API - Select a folder to sync', mustexist=True)
        return {'path': path or '', 'cancelled': not bool(path)}
    finally:
        root.destroy()


def main():
    sys.stdout.buffer.write(json.dumps(choose_folder(), ensure_ascii=False).encode('utf-8'))
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
