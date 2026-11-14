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
check(sha(before)=="33ff1ef6b16ebd2330d91ab96191fb17f45507039fa29795b4ebfb21319aff5c", "page changed")
items=json.loads("[{\"path\":\"assets/kazantsev-leonid-20261114.jpg\",\"sha\":\"705b7bfd7c21612eb1bdc05eac7ca8712610d11b512175834f43ad3dd771e9ac\",\"old\":\"<img loading=\\\"lazy\\\" decoding=\\\"async\\\" src=\\\"/assets/az-loading-20261021/image-fad27a52d50d-w320-c22bfb76dee72a76.webp\\\" alt=\\\"Казанцев Леонид Евгеньевич\\\">\",\"new\":\"<img loading=\\\"lazy\\\" decoding=\\\"async\\\" src=\\\"/assets/kazantsev-leonid-20261114.jpg\\\" style=\\\"height:auto;min-height:0;aspect-ratio:auto;object-fit:cover;object-position:center center;align-self:start\\\" alt=\\\"Казанцев Леонид Евгеньевич\\\">\"},{\"path\":\"assets/nazirov-uktamjon-20261114.jpg\",\"sha\":\"8a0d067396913b985e6e757a6d4218e8c1d5ea4b7349123db307dffa627f5b55\",\"old\":\"<img loading=\\\"lazy\\\" decoding=\\\"async\\\" src=\\\"/assets/az-loading-20261021/image-0a0e5f1378a2-w320-e88a79580a982365.webp\\\" alt=\\\"Назыров Уктамжон Жумавоевич\\\">\",\"new\":\"<img loading=\\\"lazy\\\" decoding=\\\"async\\\" src=\\\"/assets/nazirov-uktamjon-20261114.jpg\\\" style=\\\"height:auto;min-height:0;aspect-ratio:auto;object-fit:cover;object-position:center center;align-self:start\\\" alt=\\\"Назыров Уктамжон Жумавоевич\\\">\"},{\"path\":\"assets/filatova-anna-20261114.jpg\",\"sha\":\"8f78f5aa76a4e386b2c9a59ffbe307fd47509d1d37cb09f1360dcaf8dd4bb0e6\",\"old\":\"<img loading=\\\"lazy\\\" decoding=\\\"async\\\" src=\\\"/assets/filatova-20261021/portrait-approved-20261021.png\\\" alt=\\\"Филатова Анна Дмитриевна\\\">\",\"new\":\"<img loading=\\\"lazy\\\" decoding=\\\"async\\\" src=\\\"/assets/filatova-anna-20261114.jpg\\\" style=\\\"height:auto;min-height:0;aspect-ratio:auto;object-fit:cover;object-position:center center;align-self:start\\\" alt=\\\"Филатова Анна Дмитриевна\\\">\"},{\"path\":\"assets/dimitrenko-anna-20261114.jpg\",\"sha\":\"12d1a7887c357e68fed94343eeb3bc27594da0c6ca9124cf40f16b42f0880e52\",\"old\":\"<img loading=\\\"lazy\\\" decoding=\\\"async\\\" src=\\\"/assets/az-loading-20261021/image-2c18e3c29233-w320-ab558c810f1d61ef.webp\\\" alt=\\\"Димитренко Анна Яковлевна\\\">\",\"new\":\"<img loading=\\\"lazy\\\" decoding=\\\"async\\\" src=\\\"/assets/dimitrenko-anna-20261114.jpg\\\" style=\\\"height:auto;min-height:0;aspect-ratio:auto;object-fit:cover;object-position:center center;align-self:start\\\" alt=\\\"Димитренко Анна Яковлевна\\\">\"},{\"path\":\"assets/starostenko-vadim-20261114.jpg\",\"sha\":\"ed7e8cc7862283f6d7584083e6f013d6dfeacaff1645e372f4ee5a982d2ede17\",\"old\":\"<img loading=\\\"lazy\\\" decoding=\\\"async\\\" src=\\\"/assets/az-loading-20261021/image-7ae63880b5b0-w320-17595b9700b460f3.webp\\\" alt=\\\"Старостенко Вадим Анатольевич\\\">\",\"new\":\"<img loading=\\\"lazy\\\" decoding=\\\"async\\\" src=\\\"/assets/starostenko-vadim-20261114.jpg\\\" style=\\\"height:auto;min-height:0;aspect-ratio:auto;object-fit:cover;object-position:center center;align-self:start\\\" alt=\\\"Старостенко Вадим Анатольевич\\\">\"},{\"path\":\"assets/chirkov-maxim-20261114.jpg\",\"sha\":\"01ffa9b86c3d51c6964891bcf85452c13e06b4538df9f548150c1a9b79abe892\",\"old\":\"<img loading=\\\"lazy\\\" decoding=\\\"async\\\" src=\\\"/assets/az-loading-20261021/image-724c33aa843e-w320-91e9496703fec3f2.webp\\\" alt=\\\"Чирков Максим Сергеевич\\\">\",\"new\":\"<img loading=\\\"lazy\\\" decoding=\\\"async\\\" src=\\\"/assets/chirkov-maxim-20261114.jpg\\\" style=\\\"height:auto;min-height:0;aspect-ratio:auto;object-fit:cover;object-position:center center;align-self:start\\\" alt=\\\"Чирков Максим Сергеевич\\\">\"}]")
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
check(sha(after)=="62955433debe47bba40ad772f9f1a797556debf1d11dd1d5bd860894d1a8ea6e", "result hash")
stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
backup=Path("/var/backups/azmednsk")/("team-six-photos-"+stamp)
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
    fd,name=tempfile.mkstemp(prefix=".team-six-",dir=page.parent)
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
print("TEAM_SIX_PHOTOS=PASS")
print("STYLE=EXACT_ZUBACHEV; ORIGINAL_JPGS=UNCHANGED")
print("BACKUP="+str(backup))
print("URL=https://azmednsk.ru/team/#catalog")
