from pathlib import Path
from datetime import datetime,timezone
import os,hashlib,shutil,socket,tempfile
P={'preview_sha': 'aa8dc1a7421c72ab2e6792f4fa40170825c466fa520a84407dd53c71cb4c1e37', 'main_sha': '25c6ff6c24d4489da1cbe114e416c1f15fcf665fa08c81dc97b460aaba1f8cbc', 'after_sha': '4c9549db7917d6156de1ce6cf0a09459a3c4aac5120772207cc9af75fce1f502', 'assets': {'team2-admin-portrait-1-20261114.webp': 'b7599d5459bf718f67eebacee5ef8a14bbd6c0732bc7676a4525dcf933af04e8', 'team2-admin-portrait-2-20261114.webp': 'fbaf018a739fcc3ae75be191454dd3a69b4eac09d1806237b488cd7e2602f4d8', 'team2-admin-portrait-3-20261114.webp': 'aa989b33f8f7035d12d4a31d4b67403763b219e30edf6647eab8ad435730b610', 'team2-admins-1.webp': 'e099389156ac48d536424db31f783c47f46a9afc7ac25489d46a20b313616d0e', 'team2-admins-2.webp': 'e02fc23bcf949d307863e9fb8afa09fdeb222c81827e522c51a6b5ef50794923', 'team2-assistants-1.webp': 'f1ada07a3d893a1bc68b0ed66cf53248eb27a245b9413ecf5e7213455f637f7d', 'team2-besedina.webp': '08ba23870e0b144ecf5a80b94d0368e0da8ca311b0de59bb378379062a06a66e', 'team2-chirkov.webp': '9e1315ccfbe2f91df2a4caa6f456f96d4a4dec77c78b6b1d553284e6bde362d3', 'team2-dentists-2.webp': '300e47050fd9a38e5a4f2035196645bea075d311cbac3f0722d6fbcc138e68c2', 'team2-dentists-new-20261114.webp': 'c8d0884268f7a162ca01dc6c9515b800dda9c94602dbce42fe0b96062be3fe71', 'team2-dimitrenko.webp': '1274a9c26ca3879a41264f99253cac2bfbd90d2858967aa0ef4ed15477ac5498', 'team2-filatova.webp': '8127d7e9c0fe37fc95d9c3774bc1c0200382b3b1ea19551f8e486d09c797eda8', 'team2-holodtseva.webp': '9dc5e6ece8e53e5261e7690e6055bb54407d2312326bfdec4a8e11613d4e327a', 'team2-kazantsev.webp': '4b583c5821cab5bb03589fd4729844b02cf6771be5f17406134811ec0c83c905', 'team2-lab-1.webp': '0d1b9723e75eb29ddc2e45759d087f5f75b465afcc382f756e83e61289a1ae70', 'team2-lab-2.webp': '4398548852116fae2c04b6edc8df4da79c96cb096c0208544143f960ecd5a231', 'team2-lapina.webp': '6142c7f32746915f4d2c898a7456d14496df51a0006e89bdeef9ebbd9933e9bb', 'team2-lyubaeva.webp': 'dc109d73fff58bc75e4d4e444c51b3f3ed41857af461a63291c01f4177b4eaec', 'team2-mozharova-20261114.webp': 'e6f79dc8b2e73f61d01ddb97325fa5b9bd0ca0ab799766e104e3b4a22c984f65', 'team2-nazyrov.webp': '4c31976d9509cd60f539fed7e70a2ec0e1bb0b95919af1a1807e068f7383256e', 'team2-osteopaths-1.webp': '9e9671f735f6cde714953c7a1a9ae66fd8c008d38eea92efad01377c909710fa', 'team2-osteopaths-2.webp': '16eb51b1a6bf5a2578cce2303e2d6d81463cb61b7acb44d46702316a5e0a066c', 'team2-osteopaths-3.webp': '1b8ef3c88c334c55b74af43bdd274fade79be67d1c6885c4af9e61bac68383df', 'team2-palunin-20261114.webp': '700f43e6bd554190e4743436b0bbbabfc7fb8f88faf6550c73d1cf718b71ab73', 'team2-pavlova.webp': 'dbd79e0101a4c6eebb193c2f57b13c6dbde2c54cbf9d1869fc0e50fd68ace843', 'team2-starostenko.webp': 'b5f1ec59dbc7cc8f12e7079e8a70278c9557426b859b352bb0b5d16258008404', 'team2-together-1.webp': '30bf9975facb16bcfe78005fb5e0680d14f136167658e50e784689538476be83', 'team2-together-2.webp': 'f993dee8643623bb9f62ffff64e7b0661cfc1c0f07488538f9b451fc8e78d52e', 'team2-zubachev.webp': '03f34b412d473a3745efa182e62cbec81e9d119ef3584927c215ca9ebea15e7c', 'team2.css': '16a7d919f4baf90807526b981a3c9a5840e06913d005c2c575befc9e5811c9ab', 'team2.js': '81cdd962d14060bdd6ab6b64c3499c03695e91521c0e22e7b304e9c94b46ad6b'}, 'changes': [['<meta name="robots" content="noindex,nofollow,noarchive">', ''], ['<title>Команда — вариант 2 | Архитектура здоровья</title>', '<title>Команда | Архитектура здоровья</title>'], ['<div class="draft">Команда · вариант 2 · страница для обсуждения</div>', '']]}
def sha(b):return hashlib.sha256(b).hexdigest()
def check(ok,msg):
 if not ok:raise SystemExit('STOP='+msg)
def apply(root,backups):
 main=root/'team/index.html';preview=root/'team-2/index.html'
 for p in [root,root/'team',main,root/'team-2',preview,root/'assets',root/'assets/team2-v1']:
  check(p.exists() and not p.is_symlink(),'path '+str(p))
 before=main.read_bytes();source=preview.read_bytes()
 check(sha(before)==P['main_sha'],'main changed')
 check(sha(source)==P['preview_sha'],'preview changed')
 for n,h in P['assets'].items():
  p=root/'assets/team2-v1'/n
  check(p.is_file() and not p.is_symlink() and sha(p.read_bytes())==h,'asset '+n)
 text=source.decode()
 for old,new in P['changes']:
  check(text.count(old)==1,'metadata mismatch');text=text.replace(old,new,1)
 after=text.encode();check(sha(after)==P['after_sha'],'result hash')
 check(text.count('<article class="person"')==16 and 'noindex' not in text,'page invariants')
 print('PREFLIGHT=PASS; PREVIEW_EXACT=PASS; ASSETS=PASS; CARDS=16')
 backup=backups/('team-approved-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
 backup.mkdir(parents=True,exist_ok=False);shutil.copy2(main,backup/'index.html')
 check((backup/'index.html').read_bytes()==before,'backup')
 fd,name=tempfile.mkstemp(prefix='.team-approved-',dir=main.parent);tmp=Path(name)
 try:
  with os.fdopen(fd,'wb') as f:f.write(after)
  shutil.copystat(main,tmp)
  check(main.read_bytes()==before and preview.read_bytes()==source,'concurrent change')
  os.replace(tmp,main)
  check(main.read_bytes()==after,'readback')
 except BaseException:
  if main.read_bytes()==after:shutil.copy2(backup/'index.html',main)
  raise
 finally:tmp.unlink(missing_ok=True)
 print('TEAM_APPROVED_PUBLISHED=PASS; PREVIEW=UNCHANGED; INDEXABLE=YES')
 print('BACKUP='+str(backup));print('URL=https://azmednsk.ru/team/')
if __name__=='__main__':
 check(socket.gethostname()=='vdska' and os.geteuid()==0,'server/user')
 apply(Path('/var/www/azmednsk-preview'),Path('/var/backups/azmednsk'))
