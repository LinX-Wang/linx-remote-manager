"""Build clean public packages. Never use the personal migration exporter."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import manager

DOCS = ('README.md', 'LICENSE', 'SECURITY.md', 'CHANGELOG.md',
        'settings.example.json', 'VERSION', 'CONTRIBUTING.md')


def public_files():
    names = set(manager.PACKAGE_FILES) | set(DOCS)
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT / 'docs').rglob('*') if p.is_file())
    return sorted(names)


def archive(target, names, executable=None):
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as out:
        for name in names:
            path = ROOT / name
            if not path.is_file():
                raise FileNotFoundError(name)
            data = path.read_bytes()
            if name.endswith(('.command', '/MacOS/launch')):
                data = data.replace(b'\r\n', b'\n')
            elif name.endswith('.cmd'):
                data = data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            info = zipfile.ZipInfo('LinX-Remote/' + name)
            info.create_system = 3
            mode = 0o100755 if name.endswith(('.command', '/MacOS/launch')) else 0o100644
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(info, data)
        if executable:
            out.write(executable, 'LinX-Remote/' + manager.WINDOWS_APP)


def main():
    # English Windows runners default to cp1252; release filenames include Chinese.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', action='store_true')
    args = parser.parse_args()
    subprocess.run([sys.executable, str(ROOT/'tools/audit_public.py')], check=True)
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    dest = ROOT / 'dist'
    dest.mkdir(exist_ok=True)
    outputs = []
    portable = dest / ('LinX-Remote-' + version + '-portable.zip')
    archive(portable, public_files())
    outputs.append(portable)
    if not args.source_only:
        if os.name != 'nt':
            raise SystemExit('EXE 构建需要 Windows；其他系统请使用 --source-only')
        cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--onefile', '--windowed', '--name', Path(manager.WINDOWS_APP).stem,
               '--icon', str(ROOT/'LinX.ico'), '--distpath', str(dest),
               '--workpath', str(ROOT/'.build/work'), '--specpath', str(ROOT/'.build'),
               '--paths', str(ROOT)]
        for name in manager.PACKAGE_FILES:
            cmd.extend(['--add-data', str(ROOT/name) + os.pathsep + str(Path(name).parent)])
        cmd.append(str(ROOT/'launch.pyw'))
        subprocess.run(cmd, cwd=ROOT, check=True)
        executable = dest / manager.WINDOWS_APP
        win = dest / ('LinX-Remote-' + version + '-windows.zip')
        archive(win, public_files(), executable)
        outputs.extend([win, executable])
    sums = '\n'.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name for p in outputs) + '\n'
    (dest/'SHA256SUMS.txt').write_text(sums, encoding='utf-8')
    print(sums)


if __name__ == '__main__':
    main()
