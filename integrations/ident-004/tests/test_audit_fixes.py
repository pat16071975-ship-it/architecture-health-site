"""Regression tests for IDENT-004-01/02/03. No live SQL or Windows execution."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import receiver
from test_receiver import IDENTITY, OTHER, STREAM, TZ, payload, encoded


def test_sql_guard_required_before_schema_and_data():
    ps = (receiver.ROOT / 'collector.ps1').read_text()
    sql = (receiver.ROOT / 'sql/guard_readonly.sql').read_text()
    assert ps.index('$cmd.CommandText = $guardSql') < ps.index('$cmd.CommandText = $schemaSql') < ps.index('$cmd.CommandText = $sql')
    assert 'SQL_READONLY_INCOMPLETE_RESULT' in ps and 'SQL_READONLY_INVALID_RESULT' in ps
    assert 'if ($reader.GetInt32(1) -ne 1)' in ps and '$reader.NextResult()' in ps
    for name in ('identity','roles','server_effective','database_effective','server_grants','database_grants','ownership','payments_effective'):
        assert f"N'{name}'" in sql and f"'{name}'" in ps
    for scope in ('SERVER','DATABASE','OBJECT'):
        assert f"N'{scope}'" in sql
    for token in ('sys.login_token','sys.user_token','sys.server_permissions','sys.database_permissions','sys.schemas','sys.objects','owning_principal_id',"p.state = N'W'",'IS_SRVROLEMEMBER'):
        assert token in sql
    for permission in ('INSERT','UPDATE','DELETE','ALTER','CONTROL'):
        assert f"N'{permission}') = 0" in sql


def test_outbox_guard_and_file_acl_precede_writes():
    ps = (receiver.ROOT / 'collector.ps1').read_text()
    guard = (receiver.ROOT / 'outbox_guard.ps1').read_text()
    assert ps.count('Assert-PrivateOutbox $PrivateOutbox $outboxSid') == 2
    assert ps.index('Assert-PrivateOutbox $PrivateOutbox $outboxSid') < ps.index('$conn.Open()')
    assert ps.rindex('Assert-PrivateOutbox $PrivateOutbox $outboxSid') < ps.index('[System.IO.FileStream]::new(') < ps.index('Assert-PrivateAcl ($file.GetAccessControl())') < ps.index('$file.Write(')
    assert ps.index('$tmp = $candidate') > ps.index('[System.IO.FileStream]::new(')
    for token in ('AreAccessRulesProtected','AreAccessRulesCanonical','LocalApplicationData','ReparsePoint','Win32_Share','OUTBOX_SHARED_PATH','OUTBOX_SHARE_CHECK_FAILED','DriveType','NTFS','SetAccessRuleProtection($true,$false)','OUTBOX_ANCESTOR_ACL'):
        assert token in guard
    assert '[System.IO.FileMode]::CreateNew' in ps


@pytest.mark.parametrize('stage', ['before_schema', 'before_commit', 'before_publish', 'after_publish'])
def test_hard_exit_at_each_initialization_boundary(tmp_path, stage):
    db = tmp_path / 'reference.sqlite3'
    script = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import receiver as r
identity = r.Identity('11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222', 'Asia/Novosibirsk')
stage = sys.argv[3]
if stage == 'before_schema':
    r.connect = lambda *a, **kw: os._exit(91)
elif stage == 'before_commit':
    first = r.DDL[0]
    def interrupted_ddl():
        yield first
        os._exit(91)
    r.DDL = interrupted_ddl()
else:
    original = r.os.link
    def link(*a, **kw):
        if stage == 'after_publish': original(*a, **kw)
        os._exit(91)
    r.os.link = link
r.init_store(Path(sys.argv[2]), identity)
'''
    proc = subprocess.run([sys.executable, '-c', script, str(receiver.ROOT), str(db), stage], timeout=10)
    assert proc.returncode == 91
    assert db.exists() == (stage == 'after_publish')
    assert receiver.init_store(db, IDENTITY) == 'STORE_READY'
    assert receiver.ingest(db, encoded(payload())) == 'ACCEPTED'
    assert receiver.init_store(db, IDENTITY) == 'STORE_READY'
    with sqlite3.connect(db) as conn:
        assert conn.execute('select count(*) from batches').fetchone() == (1,)


@pytest.mark.parametrize('same_identity', [True, False])
def test_two_initializers_no_clobber(tmp_path, monkeypatch, same_identity):
    db = tmp_path / 'reference.sqlite3'
    barrier = Barrier(2)
    original = receiver.os.link
    def link(*a, **kw):
        barrier.wait(timeout=5)
        return original(*a, **kw)
    monkeypatch.setattr(receiver.os, 'link', link)
    other = IDENTITY if same_identity else receiver.Identity(OTHER, STREAM, TZ)
    def run(identity):
        try: return receiver.init_store(db, identity)
        except receiver.Rejected as exc: return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, IDENTITY)
        b = pool.submit(run, other)
        results = [a.result(timeout=10), b.result(timeout=10)]
    assert sorted(results) == (['STORE_READY','STORE_READY'] if same_identity else ['STORE_ALREADY_BOUND','STORE_READY'])
    assert not list(tmp_path.glob('.ident004-init-*'))
    with sqlite3.connect(db) as conn:
        assert conn.execute('select count(*) from source_identity').fetchone() == (1,)


def test_unidentified_empty_legacy_file_not_overwritten(tmp_path):
    db = tmp_path / 'reference.sqlite3'
    db.touch()
    with pytest.raises(receiver.Rejected, match='NOT_IDENT004_REFERENCE_STORE'):
        receiver.init_store(db, IDENTITY)
    assert db.read_bytes() == b''


def test_failed_publication_preserves_foreign_file(tmp_path, monkeypatch):
    db = tmp_path / 'reference.sqlite3'
    def link(candidate, target):
        target.write_bytes(b'not our database')
        raise FileExistsError
    monkeypatch.setattr(receiver.os, 'link', link)
    with pytest.raises(sqlite3.DatabaseError):
        receiver.init_store(db, IDENTITY)
    assert db.read_bytes() == b'not our database'
    assert not list(tmp_path.glob('.ident004-init-*'))


def test_link_failure_can_be_retried(tmp_path, monkeypatch):
    db = tmp_path / 'reference.sqlite3'
    with monkeypatch.context() as patch:
        def fail(*args): raise OSError('test filesystem failure')
        patch.setattr(receiver.os, 'link', fail)
        with pytest.raises(OSError): receiver.init_store(db, IDENTITY)
    assert not db.exists() and not list(tmp_path.glob('.ident004-init-*'))
    assert receiver.init_store(db, IDENTITY) == 'STORE_READY'


def test_symlink_final_path_never_replaced(tmp_path):
    db = tmp_path / 'reference.sqlite3'
    other = tmp_path / 'foreign.sqlite3'
    other.write_bytes(b'untouched')
    db.symlink_to(other)
    with pytest.raises(receiver.Rejected, match='SYMLINK_STORE_FORBIDDEN'):
        receiver.init_store(db, IDENTITY)
    assert db.is_symlink() and other.read_bytes() == b'untouched'
