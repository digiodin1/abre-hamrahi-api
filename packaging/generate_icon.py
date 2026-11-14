"""Generate the branded Windows icon with only the Python standard library."""
from pathlib import Path
import struct
import zlib


def chunk(kind, payload):
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))


def png(size):
    rows = bytearray()
    for row in range(size):
        rows.append(0)
        for column in range(size):
            horizontal, vertical = (column + .5) * 64 / size, (row + .5) * 64 / size
            corner_x, corner_y = max(15 - horizontal, 0, horizontal - 49), max(15 - vertical, 0, vertical - 49)
            color = (20, 107, 209, 255) if corner_x ** 2 + corner_y ** 2 <= 225 else (0, 0, 0, 0)
            cloud = ((horizontal - 18) ** 2 + (vertical - 34) ** 2 <= 100 or
                     (horizontal - 31) ** 2 + (vertical - 28) ** 2 <= 225 or
                     (horizontal - 44) ** 2 + (vertical - 32) ** 2 <= 144 or
                     18 <= horizontal <= 44 and 30 <= vertical <= 44)
            if cloud:
                color = (255, 255, 255, 255)
            if (24 <= vertical <= 34 and abs(horizontal - 32) <= vertical - 24 or
                    29 <= horizontal <= 35 and 32 <= vertical <= 51):
                color = (23, 171, 133, 255)
            rows.extend(color)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b'')


def build():
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [png(size) for size in sizes]
    offset, entries = 6 + 16 * len(sizes), []
    for size, payload in zip(sizes, images):
        entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(payload), offset))
        offset += len(payload)
    target = Path(__file__).resolve().parents[1] / 'src/hamrahi_backup/web/app.ico'
    target.write_bytes(struct.pack('<HHH', 0, 1, len(sizes)) + b''.join(entries) + b''.join(images))


if __name__ == '__main__':
    build()
