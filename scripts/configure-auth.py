#!/usr/bin/env python3
"""Create local credentials interactively; never use example credentials in production."""
import getpass
import os
import shlex
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security import make_password_hash

def main():
    target = Path(__file__).resolve().parents[1] / '.env'
    username = input('Choose an owner username: ').strip()
    if not username or not username.isprintable():
        raise SystemExit('A printable username is required.')
    password = getpass.getpass('Choose a password (at least 14 characters): ')
    if len(password) < 14 or password != getpass.getpass('Repeat password: '):
        raise SystemExit('Password too short or confirmation mismatch.')
    content = '\n'.join([
        'BMON_USERNAME=' + shlex.quote(username),
        'BMON_PASSWORD_HASH=' + shlex.quote(make_password_hash(password)),
        'BMON_PUBLIC_ORIGIN=http://localhost:8080',
        'BMON_HOST=127.0.0.1', 'BMON_PORT=8080',
        'BMON_DEMO=true', 'BMON_MODEL_ENABLED=false', 'BMON_VLM_ENABLED=false', 'BMON_VOICE_ENABLED=false', '',
    ])
    # Refuse to overwrite existing credentials.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(content)
    print('Created private .env. Start with scripts/run.sh; do not commit .env.')

if __name__ == '__main__':
    main()
