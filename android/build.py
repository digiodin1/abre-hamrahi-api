"""Build a small native Android APK with official SDK tools and a private key."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--jdk', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--keystore', type=Path, required=True)
    parser.add_argument('--alias', default='abre-hamrahi')
    args = parser.parse_args()
    password = os.environ.get('ABRE_ANDROID_STORE_PASSWORD')
    if not password or len(password) < 12:
        parser.error('Set ABRE_ANDROID_STORE_PASSWORD to the private signing password (12+ characters)')
    repo = Path(__file__).resolve().parents[1]
    source = repo / 'android'
    build = repo / 'build/android'
    output = repo / 'dist/android'
    if not build.resolve().is_relative_to(repo):
        raise RuntimeError('Build directory must stay inside the project')
    if build.exists():
        shutil.rmtree(build)
    for directory in (build, build / 'classes', build / 'generated', build / 'dex', output):
        directory.mkdir(parents=True, exist_ok=True)
    jdk, sdk, key = args.jdk.resolve(), args.sdk.resolve(), args.keystore.resolve()
    platform = sdk / 'platforms/android-35/android.jar'
    tools = sdk / 'build-tools/35.0.0'
    if not platform.is_file():
        platform = sdk / '35/public/android.jar'
        tools = sdk / 'tools/windows/bin'
    suffix = '.exe' if os.name == 'nt' else ''
    java = [jdk / ('bin/java' + suffix), '-Duser.language=en', '-Duser.country=US']
    jars = tools / 'lib' if (tools / 'lib').is_dir() else tools.parent / 'lib'
    version = ET.parse(source / 'AndroidManifest.xml').getroot().get('{http://schemas.android.com/apk/res/android}versionName')
    final = output / f'AbreHamrahiAPI-{version}-android.apk'

    def run(command):
        subprocess.run([str(part) for part in command], cwd=source, check=True)

    run([tools / ('aapt2' + suffix), 'compile', '--dir', source / 'res', '-o', build / 'resources.zip'])
    run([tools / ('aapt2' + suffix), 'link', '-I', platform, '--manifest', source / 'AndroidManifest.xml',
         '--java', build / 'generated', '-A', source / 'assets', '-o', build / 'unsigned.apk', build / 'resources.zip'])
    java_files = sorted((source / 'src').rglob('*.java')) + sorted((build / 'generated').rglob('*.java'))
    run([jdk / ('bin/javac' + suffix), '-encoding', 'UTF-8', '-source', '8', '-target', '8',
         '-g:none', '-classpath', platform, '-d', build / 'classes', *java_files])
    classes_jar = build / 'classes.jar'
    with zipfile.ZipFile(classes_jar, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((build / 'classes').rglob('*.class')):
            archive.write(path, path.relative_to(build / 'classes').as_posix())
    run([*java, '-cp', jars / 'd8.jar', 'com.android.tools.r8.D8', '--release', '--min-api', '26',
         '--lib', platform, '--output', build / 'dex', classes_jar])
    with zipfile.ZipFile(build / 'unsigned.apk', 'a', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((build / 'dex').glob('classes*.dex')):
            archive.write(path, path.name)
        archive.write(source.parent / 'NOTICE.md', 'assets/NOTICE.md')
    run([tools / ('zipalign' + suffix), '-f', '-p', '4', build / 'unsigned.apk', build / 'aligned.apk'])
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        run([jdk / ('bin/keytool' + suffix), '-genkeypair', '-keystore', key, '-storetype', 'JKS',
             '-storepass:env', 'ABRE_ANDROID_STORE_PASSWORD', '-keypass:env', 'ABRE_ANDROID_STORE_PASSWORD',
             '-alias', args.alias, '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10000',
             '-dname', 'CN=Abre Hamrahi API Community'])
    run([*java, '-jar', jars / 'apksigner.jar', 'sign', '--ks', key, '--ks-key-alias', args.alias,
         '--ks-pass', 'env:ABRE_ANDROID_STORE_PASSWORD', '--key-pass', 'env:ABRE_ANDROID_STORE_PASSWORD',
         '--out', final, build / 'aligned.apk'])
    run([*java, '-jar', jars / 'apksigner.jar', 'verify', '--verbose', '--print-certs', final])
    run([tools / ('zipalign' + suffix), '-c', '-p', '4', final])
    report = {'file': final.name, 'bytes': final.stat().st_size, 'sha256': hashlib.sha256(final.read_bytes()).hexdigest(),
              'minimumAndroid': '8.0', 'targetApi': 35, 'physicalDeviceTested': False}
    (output / 'build-info.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    (output / 'SHA256SUMS.txt').write_text(report['sha256'] + '  ' + final.name + '\n', encoding='ascii')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
