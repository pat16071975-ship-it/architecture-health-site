#!/usr/bin/env python3
"""Prepare a private, hash-bound period proposal from explicit owner selections.

Never writes the live database or installs the active configuration. The caller
supplies source bounds and the exact import stamp already confirmed by the owner.
No patient names/tokens, amounts, credentials or records are printed.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

root=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(root/'az-baze-auth'))
# A standalone checked download can keep the helper beside this script.
sys.path.insert(0,str(Path(__file__).resolve().parent))
import paid_period_confirmation as confirmation


def propose(conn,selections):
    if not isinstance(selections,list) or not selections:
        raise ValueError('Explicit owner-confirmed selections are required')
    sources=confirmation.read_sources(conn)
    entries=[]
    expected={'kind','sourceFrom','sourceTo','importedAt','reportMonth'}
    for item in selections:
        if not isinstance(item,dict) or set(item)!=expected:
            raise ValueError('Unexpected selection fields')
        matches=[source for source in sources if all(source[key]==item[key]
                 for key in ('kind','sourceFrom','sourceTo','importedAt'))]
        if len(matches)!=1:
            raise ValueError('Selected upload is missing, changed or ambiguous')
        source=matches[0]
        if source['payload'].get('paid_patient_evidence_complete') is not True:
            raise ValueError('Selected upload has no complete payer evidence')
        entries.append(confirmation.make_confirmation(source,item['reportMonth']))
    result={'version':1,'confirmations':entries}
    confirmation.validate_config(result)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True,type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    database=args.db.resolve(strict=True)
    selections=json.load(sys.stdin)
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=10) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        result=propose(conn,selections)
        conn.rollback()
    if args.output:
        destination=args.output.absolute()
        active=database.parent/confirmation.BASENAME
        if destination.resolve()==active or destination.name==confirmation.BASENAME:
            raise ValueError('This tool does not install the active configuration')
        raw=(json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode()
        fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        print('PRIVATE_PROPOSAL='+str(destination))
    print('CONFIRMED_MONTHS='+','.join(entry['reportMonth'] for entry in result['confirmations']))
    print('CONFIRMATION_PLAN=PASS; DATABASE_WRITES=NONE; ACTIVE_CONFIG_WRITES=NONE; DEPLOY=NONE')

if __name__=='__main__': main()
