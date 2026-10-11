#!/usr/bin/env python3
"""Read-only release guard. Reports locations/types, never matching credential values."""
from pathlib import Path
import re
import sys
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIRS = {'data','media','datasets','dataset','records','captures','trash','_trash','training','training_data','logs','runs','outputs','models','weights','checkpoints','__pycache__','.pytest_cache','.cache','.venv','venv'}
PATTERNS = {
    'private-key': rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
    'provider-key': rb'(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|hf_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,})',
    'jwt': rb'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}',
    'personal-path': rb'/(?:Users|home)/[A-Za-z0-9_.-]+/',
    'private-ip': rb'\b(?:192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b',
    'device-mac': rb'\b(?:[A-Fa-f0-9]{2}:){5}[A-Fa-f0-9]{2}\b',
}

def main():
    failures=[];count=0;images=0
    for path in ROOT.rglob('*'):
        rel=path.relative_to(ROOT)
        if '.git' in rel.parts:
            continue
        if path.is_symlink():
            failures.append((str(rel),'symlink'));continue
        if not path.is_file():
            continue
        count+=1
        if (any(part in RUNTIME_DIRS for part in rel.parts) or path.name=='.DS_Store'
                or path.name=='.env' or path.suffix in {'.env','.pyc','.db','.sqlite','.pt','.pth','.onnx','.safetensors','.pkl','.bin','.mp3','.mp4','.wav'}):
            failures.append((str(rel),'runtime-or-private-artifact'))
        data=path.read_bytes()
        if path.suffix.lower() in {'.png','.jpg','.jpeg','.webp'}:
            images+=1
            with Image.open(path) as image:
                if image.getexif() or any(k.lower() in {'exif','xmp','xml:com.adobe.xmp','comment','creation time'} for k in image.info):
                    failures.append((str(rel),'image-metadata'))
        # Scan text only; random compressed pixels can match broad regexes by chance.
        try:data.decode('utf-8')
        except UnicodeError:continue
        for kind,pattern in PATTERNS.items():
            if re.search(pattern,data):failures.append((str(rel),kind))
    for path,kind in failures:print(f'FAIL {path}: {kind} [value withheld]')
    print(f'Checked {count} files and {images} images; findings={len(failures)}')
    print('Heuristic scan only. Review asset provenance and final Git history separately.')
    return bool(failures)

if __name__=='__main__':
    sys.exit(main())
