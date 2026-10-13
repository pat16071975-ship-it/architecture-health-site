#!/usr/bin/env python3
"""Exercise canonical-only deployment and rollback on a pre-install live snapshot."""
import contextlib
import importlib.util
import io
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('apply_canonical_20261013.py'))
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class CanonicalParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonicals=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='link' and a.get('rel')=='canonical':
            self.canonicals.append(a.get('href'))

def canonicals(data):
    parser=CanonicalParser()
    parser.feed(data.decode())
    return parser.canonicals

def without_canonical(data):
    return re.sub(rb'<link rel="canonical" href="[^"]+">',b'',data)

with tempfile.TemporaryDirectory() as tmp:
    temp=Path(tmp)
    root=temp/'public'
    shutil.copytree(sys.argv[1],root)
    for name in m.PHOTOS:
        p=root/name
        p.parent.mkdir(parents=True,exist_ok=True)
        if not p.exists():p.write_bytes(b'preservation fixture '+name.encode())
    original={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert len(m.MANIFEST['files'])==75
    assert sum(not canonicals(original[n]) for n in m.MANIFEST['files'])==3
    urls=[el.text for el in ET.fromstring(original['sitemap.xml']).iter() if el.tag.endswith('}loc')]
    assert len(urls)==len(set(urls))==77

    def verify(name,data):
        assert (root/name).read_bytes()==data
        assert m.sha(data)==m.MANIFEST['files'][name]['after']
        assert canonicals(data)==[m.MANIFEST['files'][name]['canonical']]
        assert without_canonical(data)==without_canonical(original[name]),name

    def assert_original():
        actual={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
        assert actual==original,'All original bytes must be restored'

    with contextlib.redirect_stdout(io.StringIO()):
        m.apply(root,temp/'backups',verify)
        m.apply(root,temp/'backups',verify)
    assert len(list((temp/'backups').iterdir()))==1
    for url in urls:
        name=url.removeprefix('https://azmednsk.ru/')+'index.html'
        assert canonicals((root/name).read_bytes())==[url],url
    for name,data in original.items():
        if name not in m.MANIFEST['files']:assert (root/name).read_bytes()==data,name
    for name in m.MANIFEST['files']:(root/name).write_bytes(original[name])
    assert_original()

    def fail(name,data):raise RuntimeError('simulated HTTPS content mismatch')
    with contextlib.redirect_stdout(io.StringIO()):
        try:m.apply(root,temp/'rollback',fail)
        except RuntimeError:pass
        else:raise AssertionError('Failed HTTPS verification must trigger rollback')
    assert_original()

    for target in ['team/index.html','index.html']:
        (root/target).write_bytes(original[target]+b'concurrent change')
        try:m.prepare(root)
        except RuntimeError:pass
        else:raise AssertionError('Changed target or protected resource must be rejected')
        (root/target).write_bytes(original[target])
    assert_original()

print('PASS: 75 canonical-only changes; all 77 public canonical URLs match sitemap; body/metadata/styles/prices preserved')
print('PASS: apply, idempotence, unrelated files, rollback, target and protected-resource drift guards')
print('This is an isolated deployment test, not live HTTPS acceptance or confirmation of search indexing.')
