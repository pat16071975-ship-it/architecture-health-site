"""Restore archived clinic assets for a fresh Git checkout, with hash checks.

The deployable bundle already contains every asset; this is only a checkout
preparation helper and is never called by the website at runtime.
"""
import concurrent.futures, hashlib, json, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def restore(item):
    target=ROOT/'public'/item['file'].lstrip('/')
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==item['sha256']: return
    with urllib.request.urlopen(item['url'],timeout=45) as response: data=response.read()
    if hashlib.sha256(data).hexdigest()!=item['sha256']: raise RuntimeError('Source asset changed: '+item['file'])
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)

if __name__=='__main__':
    items=json.loads((ROOT/'media-manifest.json').read_text())
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(restore,items))
    print('Verified',len(items),'local media files')
