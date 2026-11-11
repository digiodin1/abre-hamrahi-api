"""Run real JVM contract tests against a fake cloud connection, without Android."""
import argparse
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--jdk', type=Path, required=True)
    parser.add_argument('--json-jar', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / 'android/src/ir/abrehamrahi/backup/community'
    classes = root / 'build/android-checks'
    classes.mkdir(parents=True, exist_ok=True)
    suffix = '.exe' if os.name == 'nt' else ''
    subprocess.run([str(args.jdk / ('bin/javac' + suffix)), '-encoding', 'UTF-8', '--release', '8',
        '-classpath', str(args.json_jar), '-d', str(classes),
        *[str(source / (name + '.java')) for name in ('UserError', 'Policy', 'Cloud')],
        str(root / 'android/tests/CloudChecks.java')], check=True)
    subprocess.run([str(args.jdk / ('bin/java' + suffix)), '-cp', os.pathsep.join([str(classes), str(args.json_jar)]),
        'ir.abrehamrahi.backup.community.CloudChecks'], check=True)


if __name__ == '__main__':
    main()
