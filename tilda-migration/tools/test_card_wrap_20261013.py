#!/usr/bin/env python3
"""Exercise deployment and rollback on a supplied pre-install HTTP snapshot."""
import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import sys
import tempfile

spec = importlib.util.spec_from_file_location('installer', Path(__file__).with_name('apply_card_wrap_20261013.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

with tempfile.TemporaryDirectory() as tmp:
    temp = Path(tmp)
    root = temp / 'public'
    shutil.copytree(sys.argv[1], root)
    for name in m.PHOTOS:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(b'preservation fixture ' + name.encode())
    original = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def verify(name, data):
        assert (root / name).read_bytes() == data
        assert m.sha(data) == m.MANIFEST['files'][name]['after']

    def assert_original():
        actual = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        assert actual == original, 'Original files must be restored; new assets must be removed'

    with contextlib.redirect_stdout(io.StringIO()):
        m.apply(root, temp / 'backups', verify)
        m.apply(root, temp / 'backups', verify)
    assert len(list((temp / 'backups').iterdir())) == 1, 'Repeat install should not create a backup'
    for name, data in original.items():
        if name not in m.MANIFEST['files']:
            assert (root / name).read_bytes() == data, ('Unexpected write', name)
    for name in m.MANIFEST['files']:
        if name in original:
            (root / name).write_bytes(original[name])
        else:
            (root / name).unlink()
    assert_original()

    def fail(name, data):
        raise RuntimeError('simulated HTTPS content mismatch')

    with contextlib.redirect_stdout(io.StringIO()):
        try:
            m.apply(root, temp / 'rollback', fail)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Failed verification must trigger rollback')
    assert_original()

    target = 'team/index.html'
    (root / target).write_bytes(original[target] + b'concurrent change')
    try:
        m.prepare(root)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Source drift must be rejected before writes')
    (root / target).write_bytes(original[target])

    asset = next(name for name, rule in m.MANIFEST['files'].items() if rule['before'] is None)
    (root / asset).write_bytes(b'unrelated existing asset')
    try:
        m.prepare(root)
    except RuntimeError:
        pass
    else:
        raise AssertionError('An unrelated asset must not be overwritten')
    (root / asset).unlink()
    assert_original()

print('PASS: apply, idempotence, unrelated file preservation, rollback including new CSS removal, source drift and asset collision guards')
print('This is an isolated deployment test, not live HTTPS or visual mobile acceptance.')
