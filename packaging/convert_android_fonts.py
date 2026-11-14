"""Derive native Android font assets from the bundled web fonts."""
from pathlib import Path


def main():
    from fontTools.ttLib import TTFont
    root = Path(__file__).resolve().parents[1]
    output = root / 'android/assets/fonts'
    output.mkdir(parents=True, exist_ok=True)
    for weight in ('regular', 'bold'):
        name = f'iransansx-{weight}'
        font = TTFont(root / 'src/hamrahi_backup/web' / (name + '.woff2'))
        font.flavor = None
        font.save(output / (name + '.ttf'), reorderTables=False)


if __name__ == '__main__':
    main()
