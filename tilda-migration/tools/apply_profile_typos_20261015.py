#!/usr/bin/env python3
"""Approved three spelling corrections in specialist profiles. No network download or Git checkout during apply."""
import argparse,concurrent.futures,fcntl,hashlib,json,os,shutil,socket,subprocess,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
MANIFEST = {'version': 1, 'files': {'team/zubachev-roman/index.html': {'before': '5fc84ffe211fdae6c3fbbec27375ac3147b380c2d40112106f9e65b25922a98a', 'after': '160fcb7d8f0055f1864e2203008f59f53a5d45fe284236c4d40c38e1e161d5fe', 'replacements': [['патологиии', 'патологии', 1]]}, 'team/lapina-natalia/index.html': {'before': 'dea1a46d5865f49e02df625ac802965e574879189e5da0c3e01be16b232985b3', 'after': '534f1f4df7c24ac9edcf4f6aa380c9e9f81f85659e9cb2bf63926f30650a4302', 'replacements': [['нейропсихолгия', 'нейропсихология', 1]]}, 'team/holodtseva-valeria/index.html': {'before': '65881be843c86023da8206e41109e5f71f1ba68360e54a44cf1002fce6df3061', 'after': '6a058d1234503b6faa17298b71d1e854165628316c816893177569e2ef6c7fea', 'replacements': [['Педогогический', 'Педагогический', 1]]}}, 'protected': {'fonts.css': '3d16ea7238b58a5cf0904098ad236aa6be0b3facccf3a53009af08fcceedce6c', 'robots.txt': '9a93da66b2f7b9bc05ad7429409692dff442b3bf8f988f2937ae21ad9cf6744f', 'site.js': '190dba2936bc2def347a43e94b81e6fe226ee77ae38220db9e4790a17c355361', 'sitemap.xml': '74ffc28b998df2bc8425c17f6b3369cf15484b9398a17dc48aef497dd87b8ba4', '404.html': '3fbe5b62b98d38eb325d3b99d47a42fd9d90fdda29ba4ae2002ceab128cab21d', 'assets/az-doctor-cards-20261013.css': '5813a1e65ce1ececfddb2d11dc6669cff3f61e16390b1b01cf870a512b3e62a0', 'assets/az-doctor-cards-wrap-20261013.css': 'b3504521129c394bb669fbc4e420ed6e86c4ed9193b1390a1ee3315dadeeec9a', 'assets/az-mozharova-approved-20261013.jpg': '621542c5ea7b609752813c546548d270d4f1848370eb1901387d48882fa98e4c', 'layouts.css': '66d78398182263a458236c439c1a098fc8390539c7d45511d9240bd54f1a3a71', 'assets/4d67d8e3617de3463719.jpg': '173ab0e4c1ad9b2f86af7545077bdcf1bde17ec73b5d46ad46253cfc3559928f', 'assets/ae539bb9ce3fbc525200.png': '79fb247028b4667e1da2316b9618a512e482bab6ea917507162658cae913f464', 'site.css': '8b2adf1b395ce968f1f116fac27b9a559d7d42265d1686737ec21dc4590406c9', 'kompleks-uslugi/korrekciya-vnimaniya-deti/index.html': 'af6154ef9b4f890217fa578f94c22151b8ddcd81a02803252c7792ad25272a9a', 'patient/dogovor/index.html': '3ec2faafc49d7e736d6c5e836a5214ecbcc46f839feaf57b9fe6ef430b1517e2', 'patient/licens/index.html': 'ca8d7feb3893dfa0610abe3aa35c298608ecac89f00b129d10ad2ea7ea1792e0', 'patient/index.html': '125e9a35e3894317a644a8e031c7e3ee52fef9df8e299dda901e479b2652eb9b', 'patient/gosgarantii/index.html': 'f1292cc4bb92de8ff108faeb91dd0a71a4ac85cb9ec18ed3a6ea1e2276153ba0', 'patient/priem-patientov/index.html': '44c2805b3d2a58c3c89babfec78f6a5060f8e4b2b6662f2bc3974edff99130f7', 'patient/patient-pravila/index.html': 'f85cd86c43ae9715ad46da07e9374355698318969f45218aa5a0e5853061ecd7', 'patient/supervision/index.html': 'b37665adb9bfab32a26f35dc389fc7edc1882b526494597c25b1a51e7e011b69', 'politica/index.html': '953a60d2d4a4e8da62be649b93ab9058adfc2fa65d1f08b03a6b66cf68992d56', 'rassrochka/index.html': '026c4a43ffdba558934a4a5c90f841962d755d5e6242d62877385a54337e8249', 'patient/zhaloba/index.html': '18520c0f806e16163416ced5321fdbbd9a3c03b04c655348c6f5c0d8aa39b638', 'team/index.html': '674e3f83578ea137a2f8c93d4c0f70eba864b1d0fc3fbe20a462a5f794531e52', 'team/movseyan-babken/index.html': '21c3a3b0dde7fcdde9062dd3d95b6edbcf3daada74d52f682f6ad4f832b165b5', 'team/rumyantseva-maria/index.html': 'c12655a39af00230af37a0d8988e4283dcbacb5eb7aac0088ebd98ec965c7fa6', 'team/tproduct/834140634503-kazantsev-leonid-evgenevich/index.html': '7a86cb4f5a475afdb0b2d92047431bc369d266d6b80a9eef7ce187518cc60d82', 'thank/index.html': '386a167e6ee4ec127635b8c81edfa0736add935cd13f9661f5dc0deb791c9746', 'team/tproduct/338106745633-palunin-vladislav-andreevich/index.html': '21cea7bdb81fe30701ed6fa8e1958b4bc68ff46d950423a28072c425a390015d', 'team/tproduct/392688125713-mozharova-ekaterina-sergeevna/index.html': 'd6f216d7c25c40956075837e827fbd55746f324412269b1bd18ab1fe3c0d54cd', 'uslugi/doc_educate/index.html': '7fd967f4fef7f2dc9d4a6d862e6cdbba769b444d1f97ac0384455f8784fe502c', 'uslugi/gastroenterologiya/index.html': 'ce935fc3944277c84ba75194632ccf0681621596c158176adbfadf28c78933f8', 'uslugi/craniodontic/index.html': '7d3a7a5ee73780ec99a306abdb9e560f56ceb95de87bc9ff52be7084a80fe2d8', 'uslugi/diers-diagnostica/index.html': '4f1244e3a79afb922c88700ffd7868a5ddcd982c19e09d0f7540ba9d7d855795', 'uslugi/diers_research/index.html': '13ada19b1e5fad3b0c1bc6ab9e44913a6448b3588d5ea68796d02404e41638c1', 'uslugi/hygiene_prevention/index.html': 'ea78a95daa7402b716ea7cbe9618b1183f8be19e67c6767de13666b3903e1ca2', 'uslugi/khirurgiya/index.html': '64235f070d9a0c517cd2f03885d1765191d19f57fff1ec23c09630f852475a5d', 'uslugi/khirurgiya/implantaciya/index.html': '7973062da1655c038c5c1dd7537be550de25c74596b5c1d0f98a54f0bbea5ca7', 'uslugi/iggt-terapiya/index.html': '9633fcf195bdcc0bd87555593f0b7ba249f0b49a3584fb963da8af39e1b312b9', 'uslugi/konsultatiya-stomatolog/index.html': 'fa5c8bca8f04848045973571f859691501785f398559f5c0bce6e1f059f71065', 'uslugi/hygiene_prevention/otbelivanie_zoom4/index.html': 'f1125457e476efb53e3dfaca58e1bd68b5d7e502af6d3e5a8ca7eef46d7b349a', 'uslugi/lechenie-zubov/pulpit/index.html': '3813fbcc5800bc370197c899cada8dd254541efe85ce609dc0de81dfca789aa8', 'uslugi/lechenie-zubov/kista/index.html': 'f9de5c211c81801eaac3ce8e01021bc44180d4965fbb416a6ef42dea3767d95f', 'uslugi/lechenie-zubov/esteticheskaya_restavraciya/index.html': 'b76090b0d971a6d53c21b5dc71b7c6c6375bff446fc2b11ffff753e97e6d596e', 'uslugi/lechenie-zubov/karies/index.html': '71153a80cc966dc75d269890050eaf728e6f01efc4acd8dd3222346a7044e407', 'uslugi/lechenie-zubov/periodontit/index.html': '3d1ca3f8957875e15a3f7c8b2fe8a54c11004210abaeb47f7ebae3c010ba491b', 'uslugi/lechenie-zubov/index.html': '105059c23daec27d9dd07dccc430f3de42953174ac9ee2d7eb5bef32c19adc56', 'uslugi/lechenie-zubov/udalenie_zubnogo_kamnya/index.html': '7b98813b4ef4be6b30cb9c0e962e003ec7af8d28dad86f17d664ec110b4fa80a', 'uslugi/lechenie-zubov/zuby-mudrosti/index.html': 'b76d1de377b8423fc049cf45687dbfd6e74cbb95e73e24545a8a8d5da822e664', 'uslugi/miofascialnyy-massazh/index.html': '7d94717ce64385785dca8983d7144a062cdb8411dd8c0f786bf64fdb0b67cef3', 'uslugi/miofunkcionalnaya-terapiya/index.html': '19311e29ddb0f4b548b47232b6c262b7a853b1657a437ecbeb3ee7179e2e51e4', 'uslugi/neyropsihologia/index.html': '432cc8f61afcb54acf89fe5a883dd9964485e70aad33503163fe0af2c2628ad5', 'uslugi/nutriciologiya/index.html': '39d164ee3f31364f5f65182051cbbdd3373f8c44a263a123afab0102433f2fe3', 'uslugi/prosthetics/index.html': '40fbd68c1cbd9aabb5a886c4151de7dcba3665add7cd59bf1cc83178ac38c08d', 'uslugi/ortopedicheskie-stelki/index.html': '7b6328fba1144c396f30e00f848f7109c412fcf8c357db562e177aabb0d73367', 'uslugi/orthodontics/index.html': '579fbc5ed969b5611a834de592582b788e6883f58c65604cf103acf9369602c2', 'uslugi/preventivnaya-medicina/index.html': '7bb920592f13e41983c48474f134d8e9818d79811531cd2763cbe4b86c58b1b1', 'uslugi/prosthetics/koronki/index.html': 'a46555759985405df330474d0574b5dccfa22cae9f2010f0e9df9fa8ab8d6dc6', 'uslugi/terapia/index.html': '413b5946544a1a58b97962bdedc78dea7c6a3d01280b782dbb1eca604e0741a8', 'uslugi/prosthetics/koronki/keramicheskie/index.html': '3919a2a43a07112a60f1ef3dbb9fe3ef88eb573c7562592b9cdd504ce9a3c749', 'uslugi/prosthetics/mostovidnyy-protez/index.html': 'af6a39534591f28cdd0427de54d8a70ea2e6b1797aa722a0dc9daee16439ce9c', 'uslugi/prosthetics/viniry/index.html': '9630055a9fce7004145b49e493800eea827e836fe5bfa02cd64de175930f97e8', 'uslugi/prosthetics/koronki/kompozitnye/index.html': 'c75276c64ee90985f60835c16bbe1df3822a534549f20425d29b1e4c1062933b', 'uslygi/index.html': '25ebc091620267e3513246e6edb62a56a98e79add1b8068610233b8858eb07e4', 'team/starostenko-vadim/index.html': '1a9339c681f525ac0837753b6ae613ff71de352cfd2ba4d209288cacf48bd4c1', 'team/dimitrenko-anna/index.html': '55dcccd3de4dc1c31c662833f40de427c5a095248b56d05d3acfd373260e0fe3', 'team/mamontova-lubava/index.html': 'ee675b3c5f365152d4eacebe0ce817ad229cebaac592a7e07d4292878418e93e', 'team/chirkov-maxim/index.html': '86ff259875603dd0785800123187e92b13da403b3d4f54a3f19c64364dcd139f', 'team/bosak-yana/index.html': '139b1f557dd678de255f7be079a3bb9964e55ac98fbd15456009ee03709d34c2', 'team/nazirov-uktamjon/index.html': 'fc2c3e8215f06478a1cdbc7dc1748afe96073986f934632a86ee5bd66ca90f05', 'team/golishenkov-vladislav/index.html': '995fe0eef209f3fbebb68d96167b4afba98e694b3e63615f44257e6719d85981', 'team/lubaeva-alena/index.html': '4c931ef6980ab985a083f6343b4a470914dd9eecf6f66e4218c0e144dfe6b715', 'patient/patient-rules/index.html': '6ce3d9be10fb4279ac0fd11dabf48509b690941b7c91d58a880efb06c897c075', 'contacts/index.html': '97f7df819dc19efe4cdfb0828f571c16ccd0cf26a1c9b72696dc75600ad01192', 'cookie/index.html': 'ac7a02be3e558e947871f4aa18249c6ef590169e414e36493cf63f34606a3c9d', 'lab/index.html': '58c0b68275cbebeb5b0a61769cb18d5a00a698921ce05fe13f1230e1e12490ad', 'politica-site/index.html': '49e9228568b6bd05cf837e8d801b637b54973fc712f70af4f797e5665bec70b7', 'index.html': '2a9fbf7465556a3636bacb13fc2f9d2750437b16107b9213ea8df47c5f8c760d', 'otziv/index.html': '83562de1a29e2c4ee2a02eb428eaa9f572b9e76d4dea69e74bcea3d3fdaa0103', 'kompleks-uslugi/index.html': '682aa1b0033fb7eb6cf318186091374b2ce8677d6c16b80cafe1ebf19c1339e1', 'kompleks-uslugi/zdorovaya-spina/index.html': '3352dcb0191c1f75b0146ef73388330ec652f5d83e26f5a730f6df00124ceab4', 'kompleks-uslugi/arhitektura-zdorovya/index.html': '7282cff12e12ce84353198c721b3d886ed89e8e0a67366059941afbf222c1cd2', 'team/pavlova-anna/index.html': 'f70abc13df2f30362f473a61a9762f3334266504bfd658da8f3ad4c2e9379adb', 'o_nas/index.html': 'c4aa17d487e6057c53ee3b6fe035fa4d3aeef5fbfd877d78801ddbe01ebb2e30', 'uslugi/functional_dentistry/index.html': '0031214fb23f74a9468fd053e80fcc7c72c5d3449e0cf28b28b0086ef530b7f3', 'uslugi/osteopatiya/index.html': '603c97d046f456b65e5e5634e1c491a9eb26ae326736752463909ce8a29ca249', 'uslugi/miofascialnaya-terapia/index.html': 'b68956a4111941ea7d2e8911190ffe894fd3cbd4b3d35644aab815c855b8fd14'}}
ROOT=Path('/var/www/azmednsk-test/tilda-migration/public')
BACKUPS=Path('/var/backups/azmednsk')
PHOTOS=('assets/4ab8539d240e65119fbc.jpg','assets/594800af226e87bb24de.jpg','assets/560ccf811a455b6ce7b0.jpeg','assets/3a23cefb232c54a024e6.jpg')
def sha(b):return hashlib.sha256(b).hexdigest()
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def prepare(root):
 result={}
 for name,rule in MANIFEST['files'].items():
  p=root/name
  require(not p.is_symlink() and p.resolve().is_relative_to(root.resolve()),'Unsafe path: '+name)
  old=p.read_bytes() if p.exists() else None
  digest=sha(old) if old is not None else None
  require(digest in (rule['before'],rule['after']),'Изменён исходный файл: '+name)
  if digest==rule['after']:new=old
  elif 'content' in rule:new=rule['content'].encode()
  else:
   s=old.decode()
   for a,b,count in rule.get('replacements',[]):
    require(count>0 and s.count(a)==count,'Неоднозначная замена: '+name);s=s.replace(a,b)
   s+=rule.get('append','');new=s.encode()
  require(sha(new)==rule['after'],'Неверный результат: '+name)
  result[name]=(old,new,p.stat() if old is not None else None)
 for name,digest in MANIFEST['protected'].items():require(sha((root/name).read_bytes())==digest,'Изменена служебная страница: '+name)
 return result

def atomic(p,data,st):
 fd,tmp=tempfile.mkstemp(prefix='.az-profile-typos-',dir=p.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  owner=st or p.parent.stat()
  os.chmod(tmp,(st.st_mode & 0o7777) if st else 0o644);os.chown(tmp,owner.st_uid,owner.st_gid)
  os.replace(tmp,p)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def verify_http(name,expected):
 path=MANIFEST['files'][name].get('url',name.removesuffix('index.html') if name.endswith('index.html') else name).lstrip('/')
 with tempfile.TemporaryDirectory() as td:
  h=Path(td)/'headers';b=Path(td)/'body'
  r=subprocess.run(['curl','--noproxy','*','--resolve','azmednsk.ru:443:127.0.0.1','--connect-timeout','5','--max-time','20','-fsS','-w','%{http_code}','-D',str(h),'-o',str(b),'-H','Cache-Control: no-cache','https://azmednsk.ru/'+path],capture_output=True)
  require(r.returncode==0 and r.stdout.strip()==b'200','HTTPS failed: '+path)
  require(b.read_bytes()==expected,'HTTPS content mismatch: '+path)
  if name.endswith('.html') and name!='thank/index.html':
   for line in h.read_text().splitlines():
    if line.lower().startswith('x-robots-tag:'):require('noindex' not in line.lower() and 'none' not in line.lower(),'X-Robots-Tag blocks indexing: '+path)

def verify_all(prepared,verify):
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
  list(pool.map(lambda item:verify(item[0],item[1][1]),prepared.items()))

def apply(root,backups,verify=verify_http):
 prepared=prepare(root)
 protected={n:(root/n).read_bytes() for n in (*PHOTOS,*MANIFEST['protected'])}
 changes={n:v for n,v in prepared.items() if v[0]!=v[1]}
 if not changes:
  verify_all(prepared,verify);print('ALREADY_INSTALLED=PASS; HTTPS=PASS');return
 backup=backups/('profile-typos-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700)
 for n,(old,new,st) in prepared.items():
  if old is not None:
   p=backup/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/n,p);require(p.read_bytes()==old,'Backup mismatch: '+n)
 (backup/'manifest.json').write_text(json.dumps(MANIFEST,ensure_ascii=False,indent=2))
 (backup/'absent-before.json').write_text(json.dumps([n for n,v in prepared.items() if v[0] is None]))
 for n,(old,_,_) in prepared.items():require(((root/n).read_bytes() if (root/n).exists() else None)==old,'Concurrent change: '+n)
 written=[]
 try:
  for n,(_,new,st) in changes.items():atomic(root/n,new,st);written.append(n)
  for n,(_,new,_) in prepared.items():require((root/n).read_bytes()==new,'Write mismatch: '+n)
  verify_all(prepared,verify)
  for n,b in protected.items():require((root/n).read_bytes()==b,'Protected content changed: '+n)
 except BaseException:
  failures=[]
  for n in reversed(written):
   old,new,st=changes[n]
   try:
    require((root/n).read_bytes()==new,'Concurrent change blocks rollback')
    if old is None:(root/n).unlink()
    else:atomic(root/n,old,st);os.utime(root/n,ns=(st.st_atime_ns,st.st_mtime_ns))
    require(((root/n).read_bytes() if (root/n).exists() else None)==old,'Rollback mismatch')
   except Exception as e:failures.append(n+': '+str(e))
  print('BACKUP='+str(backup));print('ROLLBACK_FILES='+('PASS' if not failures else 'FAIL '+str(failures)));raise
 print('PROFILE_TYPOS=PASS; HTTPS=PASS; PHOTOS_AND_PRICES=UNCHANGED')
 print('HTML_UPDATED=3; TYPO_REPLACEMENTS=3')
 print('LAYOUT_AND_FONTS=UNCHANGED; LINKS_AND_CANONICAL=UNCHANGED')
 print('BACKUP='+str(backup))
 print('Следующий шаг: пришлите вывод для внешней проверки сайта.')
def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--check',action='store_true');g.add_argument('--apply',action='store_true');a=p.parse_args()
 require(socket.gethostname()=='az-server','Only az-server');require(os.geteuid()==0,'Use sudo python3');require(ROOT.is_dir(),'Missing site root')
 if a.check:prepare(ROOT);print('PREFLIGHT=PASS; LIVE_WRITES=NONE');return
 with open('/run/lock/azmednsk-critical.lock','w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);apply(ROOT,BACKUPS)
if __name__=='__main__':
 try:main()
 except Exception as e:print('STOP:',e,file=sys.stderr);sys.exit(1)
