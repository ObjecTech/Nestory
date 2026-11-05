#!/usr/bin/env python3
"""Backup ONLY after the human maintenance-start instruction; never automatic."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maintenance.assistant_test_backup import backup_sqlite

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True,help='Actual active SQLite file, confirmed from process FD/config')
    parser.add_argument('--destination',type=Path,required=True,help='New filename in a private backup directory')
    parser.add_argument('--maintenance-started',action='store_true')
    args=parser.parse_args()
    print(json.dumps(backup_sqlite(args.source,args.destination,maintenance_started=args.maintenance_started)))
