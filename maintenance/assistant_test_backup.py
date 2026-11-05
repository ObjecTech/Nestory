"""Explicit maintenance-only SQLite backup; never called by harness startup."""
from pathlib import Path
from contextlib import closing
import hashlib
import os
import sqlite3
import time
from urllib.parse import quote


def backup_sqlite(source,destination,*,maintenance_started=False,timeout=30):
    if not maintenance_started:
        raise ValueError('Separate human maintenance-start instruction required before production backup')
    source=Path(source).absolute(); destination=Path(destination).absolute()
    for path in (source,destination):
        if any(item.is_symlink() for item in (path,*path.parents)):
            raise ValueError('Backup paths must not be symlinks')
    if source==destination or not source.is_file() or source.stat().st_size==0:
        raise ValueError('Select the actual active nonempty SQLite file')
    if destination.parent.stat().st_mode & 0o077:
        raise ValueError('Backup destination directory must be private')
    # O_EXCL avoids overwriting an earlier recovery point; source is read-only.
    fd=os.open(destination,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600); os.close(fd)
    started=time.monotonic()
    def progress(status,remaining,total):
        if time.monotonic()-started>timeout:
            raise TimeoutError('Backup deadline; partial output is not a recovery point')
    try:
        with closing(sqlite3.connect('file:'+quote(str(source))+'?mode=ro',uri=True,timeout=3)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst,pages=128,progress=progress,sleep=.01)
                if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('Backup integrity failed')
        digest=hashlib.sha256()
        with destination.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
        return {'complete':True,'bytes':destination.stat().st_size,'sha256':digest.hexdigest()}
    except BaseException:
        # Keep failed output as evidence, never advertise it as a usable snapshot.
        failed=destination.with_name(destination.name+'.incomplete')
        if not failed.exists(): destination.rename(failed)
        raise
