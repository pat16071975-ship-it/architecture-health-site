#!/usr/bin/env python3
"""Isolated deployment, preservation and failure tests; argument is the live baseline directory."""
import contextlib
import importlib.util
import io
from html.parser import HTMLParser
from pathlib import Path
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('apply_loading_20261013.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Tags(HTMLParser):
    def __init__(self,data):
        super().__init__();self.images=[];self.fonts=[];self.canonical=[];self.robots=[];self.feed(data.decode())
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='img':self.images.append(a)
        if tag=='link' and a.get('rel')=='stylesheet' and '/fonts.css' in a.get('href',''):self.fonts.append(a['href'])
        if tag=='link' and a.get('rel')=='canonical':self.canonical.append(a.get('href'))
        if tag=='meta' and a.get('name')=='robots':self.robots.append(a.get('content'))

with tempfile.TemporaryDirectory() as tmp:
    temp=Path(tmp);root=temp/'public';shutil.copytree(sys.argv[1],root)
    for name in m.PHOTOS:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
        if not p.exists():p.write_bytes(b'preservation fixture '+name.encode())
    original={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert len(m.MANIFEST['files'])==79
    assert len([n for n in m.MANIFEST['files'] if n.endswith('.html')])==78
    version=m.MANIFEST['font_version'];font_url='/fonts.css?v='+version
    hero='/assets/4d67d8e3617de3463719.jpg'

    def verify(name,data):
        assert (root/name).read_bytes()==data
        assert m.sha(data)==m.MANIFEST['files'][name]['after']
        if name=='fonts.css':
            assert data.count(b'font-display: swap;')==9
            assert data.replace(b'  font-display: swap;\n',b'')==original[name]
            assert m.sha(data).startswith(version)
            return
        old,new=Tags(original[name]),Tags(data)
        assert old.fonts==['/fonts.css'] and new.fonts==[font_url]
        assert old.canonical==new.canonical and old.robots==new.robots
        restored=data.replace(font_url.encode(),b'/fonts.css')
        if name=='index.html':
            images=[i for i in new.images if i.get('src')==hero]
            assert len(images)==1 and images[0]['loading']=='eager' and images[0]['fetchpriority']=='high'
            assert sum(i.get('fetchpriority')=='high' for i in new.images)==1
            restored=restored.replace(b'loading="eager" fetchpriority="high"',b'loading="lazy"')
            for i in new.images:
                if i.get('src')==hero:i['loading']='lazy';del i['fetchpriority']
        assert old.images==new.images
        assert restored==original[name],name

    def assert_original():
        actual={p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
        assert actual==original,'All original bytes must be restored'

    with contextlib.redirect_stdout(io.StringIO()):
        m.apply(root,temp/'backups',verify)
        m.apply(root,temp/'backups',verify)
    assert len(list((temp/'backups').iterdir()))==1
    urls=[el.text for el in ET.fromstring(original['sitemap.xml']).iter() if el.tag.endswith('}loc')]
    assert len(urls)==len(set(urls))==77
    for url in urls:
        name=url.removeprefix('https://azmednsk.ru/')+'index.html'
        assert Tags((root/name).read_bytes()).canonical==[url],url
    assert 'noindex' in ','.join(Tags((root/'thank/index.html').read_bytes()).robots)
    for name,data in original.items():
        if name not in m.MANIFEST['files']:assert (root/name).read_bytes()==data,name
    for name in m.MANIFEST['files']:(root/name).write_bytes(original[name])
    assert_original()

    def fail(name,data):raise RuntimeError('simulated HTTPS failure')
    with contextlib.redirect_stdout(io.StringIO()):
        try:m.apply(root,temp/'rollback-http',fail)
        except RuntimeError as e:assert 'simulated HTTPS' in str(e)
        else:raise AssertionError('HTTPS failure must roll back')
    assert_original()

    original_atomic=m.atomic;writes=0
    def fail_once(path,data,st):
        global writes
        writes+=1
        if writes==4:raise OSError('simulated disk write failure')
        return original_atomic(path,data,st)
    m.atomic=fail_once
    with contextlib.redirect_stdout(io.StringIO()):
        try:m.apply(root,temp/'rollback-write',verify)
        except OSError as e:assert 'simulated disk' in str(e)
        else:raise AssertionError('Partial write must roll back')
    m.atomic=original_atomic;assert_original()

    for target in ['fonts.css','index.html','team/index.html','sitemap.xml','site.css']:
        (root/target).write_bytes(original[target]+b'concurrent change')
        try:m.prepare(root)
        except RuntimeError:pass
        else:raise AssertionError('Changed target/protected resource must be rejected: '+target)
        (root/target).write_bytes(original[target])
    assert_original()

print('PASS: 9 font faces use swap; 78 versioned links; single eager/high home hero')
print('PASS: all other HTML, images, prices, styles, canonical and robots preserved; sitemap 77')
print('PASS: apply, idempotence, full and partial-write rollback, target/protected drift guards')
print('Isolated fixture test only; external HTTPS and browser acceptance follow installation.')
