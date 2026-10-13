#!/usr/bin/env python3
"""Installer safety checks against a fresh read-only HTTP snapshot, not live deployment."""
import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import sys
import tempfile

spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('apply_content_corrections_20261013.py'))
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

with tempfile.TemporaryDirectory() as tmp:
    temp=Path(tmp)
    root=temp/'public'
    shutil.copytree(sys.argv[1],root)
    for name in m.PHOTOS:
        path=root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'preservation fixture '+name.encode())
    original={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    def verify(name,data):
        assert (root/name).read_bytes()==data
        assert m.sha(data)==m.MANIFEST['files'][name]['after']
    with contextlib.redirect_stdout(io.StringIO()):
        m.apply(root,temp/'backups',verify)
        m.apply(root,temp/'backups',verify)
    for name,data in original.items():
        if name not in m.MANIFEST['files']:
            assert (root/name).read_bytes()==data,('Unexpected write',name)
    for name in m.MANIFEST['files']:
        (root/name).write_bytes(original[name])
    def fail(name,data):
        raise RuntimeError('simulated HTTPS content mismatch')
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            m.apply(root,temp/'rollback',fail)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Failed verification must trigger rollback')
    for name,data in original.items():
        assert (root/name).read_bytes()==data,('Rollback mismatch',name)
    target='team/lubaeva-alena/index.html'
    (root/target).write_bytes(original[target]+b'concurrent change')
    try:
        m.prepare(root)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Unexpected source must be rejected')
    (root/target).write_bytes(original[target])
    (root/'unexpected.html').write_text('<p>Бесплатная консультация</p>')
    try:
        m.prepare(root)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Additional unreviewed free-service claim must be rejected')

print('PASS: apply, idempotence, unchanged unrelated files, verification-failure rollback, source drift guard, additional-claim guard')
print('Protected photo checks use preservation fixtures; this test is not live HTTPS or visual acceptance.')
