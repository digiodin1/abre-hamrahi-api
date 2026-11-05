"""Open the native folder dialog in a dedicated GUI process."""
import json
import sys
import tkinter
from tkinter import filedialog


def main():
    root = tkinter.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        path = filedialog.askdirectory(parent=root, title='Abre Hamrahi API - Select a folder to sync', mustexist=True)
        sys.stdout.buffer.write(json.dumps({'path': path or '', 'cancelled': not bool(path)}, ensure_ascii=False).encode('utf-8'))
        sys.stdout.buffer.flush()
    finally:
        root.destroy()


if __name__ == '__main__':
    main()
