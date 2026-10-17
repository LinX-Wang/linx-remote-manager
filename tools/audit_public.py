"""Conservative, offline checks for the public tree and release whitelist."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {'.git', '.build', 'build', 'dist', '__pycache__', '.venv', 'venv'}
PRIVATE_NAMES = {'settings.json', 'auth.json', 'config.toml', '.env', 'profiles.json',
                 'ui-instance.json', 'known_hosts', 'id_rsa', 'id_ed25519'}
PATTERNS = [
    ('private key', re.compile(r'-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----')),
    ('API credential', re.compile(r'\bsk-[A-Za-z0-9_-]{20,}')),
    ('GitHub credential', re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})')),
    ('private IPv4', re.compile(r'(?<![\d.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![\d.])')),
    ('author machine path', re.compile(r'(?:[A-Za-z]:[\\/]Users[\\/]|/disk\d+/|/Users/[A-Za-z0-9_.-]+/)')),
]


def files():
    # If this is a Git worktree, include tracked files even when .gitignore matches them.
    if (ROOT/'.git').exists():
        out = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT)
        return [ROOT/p.decode('utf-8') for p in out.split(b'\0') if p]
    return [p for p in ROOT.rglob('*') if p.is_file() and not (set(p.relative_to(ROOT).parts) & IGNORED)]


def audit():
    issues = []
    for path in files():
        relative = path.relative_to(ROOT).as_posix()
        if path.name in PRIVATE_NAMES or path.suffix.lower() in {'.pem', '.key', '.bak', '.old', '.exe', '.zip', '.log'}:
            issues.append(relative + ': personal/runtime artifact')
        if path.suffix.lower() in {'.png', '.ico', '.icns'}:
            continue
        try:
            raw = path.read_bytes()
            source = raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8')
        except UnicodeError:
            issues.append(relative + ': unexpected binary')
            continue
        for label, pattern in PATTERNS:
            if pattern.search(source):
                issues.append(relative + ': ' + label)
    return issues


if __name__ == '__main__':
    issues = audit()
    if issues:
        print('\n'.join(issues))
        sys.exit(1)
    print('Public tree audit passed (not a substitute for manual review).')
