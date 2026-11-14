from pathlib import Path
from datetime import datetime, timezone
import urllib.request, hashlib, shutil, os, socket, sys, json, tempfile

def check(ok, message):
    if not ok: raise SystemExit("STOP=" + message)
def sha(b): return hashlib.sha256(b).hexdigest()
check(socket.gethostname()=="vdska" and os.geteuid()==0, "server/user")
root=Path("/var/www/azmednsk-preview")
page=root/"team/index.html"
check(not page.is_symlink(), "page symlink")
before=page.read_bytes()
check(sha(before)=="653022953e57d48ee82cb49da72edf1be83ebe2532b53543d7abd78fe4bb8633", "page changed")
items=json.loads("[{\"path\":\"assets/zubachev-roman-20261114-640.webp\",\"sha\":\"ee1cd5ba7ad8f5c54922a39cc33175a3d4e4b2f8b36c760026f944354cb5207b\",\"old\":\"/assets/zubachev-roman-20261114.jpg\",\"new\":\"/assets/zubachev-roman-20261114-640.webp\"},{\"path\":\"assets/kazantsev-leonid-20261114-640.webp\",\"sha\":\"2750aad9daac4bb4491ecbdc271bd782bb7a2c57434922016bb736d567256b29\",\"old\":\"/assets/kazantsev-leonid-20261114.jpg\",\"new\":\"/assets/kazantsev-leonid-20261114-640.webp\"},{\"path\":\"assets/nazirov-uktamjon-20261114-640.webp\",\"sha\":\"6fec523a1de1276133274b3f509a243cb3ddc24706c5cbb0dd73bf23add542d4\",\"old\":\"/assets/nazirov-uktamjon-20261114.jpg\",\"new\":\"/assets/nazirov-uktamjon-20261114-640.webp\"},{\"path\":\"assets/filatova-anna-20261114-640.webp\",\"sha\":\"d57d6d8648c9c756c5bdecca449ab0f318f0214b12956cb1741e6fc21b088407\",\"old\":\"/assets/filatova-anna-20261114.jpg\",\"new\":\"/assets/filatova-anna-20261114-640.webp\"},{\"path\":\"assets/dimitrenko-anna-20261114-640.webp\",\"sha\":\"6d7fb1d452049b54a39d6ebd2e93360c182e685a3ca266a81cb51593e8dba8f8\",\"old\":\"/assets/dimitrenko-anna-20261114.jpg\",\"new\":\"/assets/dimitrenko-anna-20261114-640.webp\"},{\"path\":\"assets/starostenko-vadim-20261114-640.webp\",\"sha\":\"549c75181c85b5fe40df385016ff94cd5e7508283b2924abfcf2175d89ff199a\",\"old\":\"/assets/starostenko-vadim-20261114.jpg\",\"new\":\"/assets/starostenko-vadim-20261114-640.webp\"},{\"path\":\"assets/chirkov-maxim-20261114-640.webp\",\"sha\":\"5f707eb8800ec861780c3c0b6b426ebddd72299797b311fb4d05fc5a6ccccd67\",\"old\":\"/assets/chirkov-maxim-20261114.jpg\",\"new\":\"/assets/chirkov-maxim-20261114-640.webp\"},{\"path\":\"assets/besedina-maria-20261114-640.webp\",\"sha\":\"2d130e4cc01770a2e5a518d9cd3b21bb442bcd280e326dd32f002512ebcf3433\",\"old\":\"/assets/besedina-maria-20261114.jpg\",\"new\":\"/assets/besedina-maria-20261114-640.webp\"}]")
base="https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/"+sys.argv[1]+"/tilda-migration/public/"
after=before.decode()
downloads=[]
for item in items:
    check(after.count(item["old"])==1, "image match")
    after=after.replace(item["old"], item["new"], 1)
    dest=root/item["path"]
    check(not dest.exists() and not dest.is_symlink(), "asset already exists")
    data=urllib.request.urlopen(base+item["path"],timeout=60).read()
    check(sha(data)==item["sha"], "asset hash")
    downloads.append((dest,data))
after=after.encode()
check(sha(after)=="25c6ff6c24d4489da1cbe114e416c1f15fcf665fa08c81dc97b460aaba1f8cbc", "result hash")
stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
backup=Path("/var/backups/azmednsk")/("team-webp-photos-"+stamp)
backup.mkdir(parents=True,exist_ok=False)
shutil.copy2(page,backup/"index.html")
check((backup/"index.html").read_bytes()==before,"backup")
created=[]
tmp=None
try:
    for dest,data in downloads:
        with dest.open("xb") as f: f.write(data)
        created.append(dest)
        os.chmod(dest,0o644)
        check(sha(dest.read_bytes())==sha(data),"written asset")
    fd,name=tempfile.mkstemp(prefix=".team-webp-",dir=page.parent)
    tmp=Path(name)
    with os.fdopen(fd,"wb") as f: f.write(after)
    shutil.copystat(page,tmp)
    st=page.stat()
    os.chown(tmp,st.st_uid,st.st_gid)
    check(page.read_bytes()==before,"concurrent change")
    os.replace(tmp,page)
except BaseException:
    if page.read_bytes()==before:
        for dest in created: dest.unlink()
    raise
finally:
    if tmp and tmp.exists(): tmp.unlink()
check(page.read_bytes()==after,"installed page")
print("TEAM_WEBP_OPTIMIZATION=PASS")
print("STYLE=EXACT_ZUBACHEV; ORIGINAL_JPGS=UNCHANGED")
print("BACKUP="+str(backup))
print("URL=https://azmednsk.ru/team/#catalog")
