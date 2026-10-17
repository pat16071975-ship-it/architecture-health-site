#!/usr/bin/env python3
"""Apply five approved massage text replacements with exact guards and rollback."""
import argparse,concurrent.futures,fcntl,hashlib,json,os,shutil,socket,subprocess,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
MANIFEST = {'version': 1, 'purpose': 'Apply five approved wording replacements in massage page main text; preserve testimonials', 'approved_at': '2026-10-17T18:36:24+07:00', 'files': {'uslugi/miofascialnyy-massazh/index.html': {'before': 'ec86621946b3a088be009f3d876364b15fa06e07dc74b4823d798fe1702ec3ca', 'after': '9a7fc4ac21bb1ac33967f20900e6d4c539389f557975c1c8b3a8af6f872ff820', 'replacements': [['сертифицированные мастера', 'сертифицированные специалисты', 1], ['в нашей студии', 'в нашей клинике', 1], ['мастера с медицинским образованием и сертификатами', 'специалисты с медицинским образованием и сертификатами', 1], ['студия в центре Новосибирска', 'клиника в центре Новосибирска', 1], ['Наши мастера', 'Наши специалисты', 1]]}}, 'protected': {'sitemap.xml': '7d25e8f24835ef0772c360356f8ee9c350921cb602b155d836713e00b05b40a2', 'fonts.css': '3d16ea7238b58a5cf0904098ad236aa6be0b3facccf3a53009af08fcceedce6c', 'robots.txt': '9a93da66b2f7b9bc05ad7429409692dff442b3bf8f988f2937ae21ad9cf6744f', 'site.js': '190dba2936bc2def347a43e94b81e6fe226ee77ae38220db9e4790a17c355361', '404.html': '3fbe5b62b98d38eb325d3b99d47a42fd9d90fdda29ba4ae2002ceab128cab21d', 'assets/az-doctor-cards-20261013.css': '5813a1e65ce1ececfddb2d11dc6669cff3f61e16390b1b01cf870a512b3e62a0', 'assets/az-doctor-cards-wrap-20261013.css': 'b3504521129c394bb669fbc4e420ed6e86c4ed9193b1390a1ee3315dadeeec9a', 'assets/az-mozharova-approved-20261013.jpg': '621542c5ea7b609752813c546548d270d4f1848370eb1901387d48882fa98e4c', 'layouts.css': '66d78398182263a458236c439c1a098fc8390539c7d45511d9240bd54f1a3a71', 'assets/4d67d8e3617de3463719.jpg': '173ab0e4c1ad9b2f86af7545077bdcf1bde17ec73b5d46ad46253cfc3559928f', 'assets/ae539bb9ce3fbc525200.png': '79fb247028b4667e1da2316b9618a512e482bab6ea917507162658cae913f464', 'site.css': '8b2adf1b395ce968f1f116fac27b9a559d7d42265d1686737ec21dc4590406c9', 'assets/2eb303492fc4afc1e2bc.png': 'fad27a52d50d0e7bb74f40ca28841adf1e3525421d111f197afd5599fc03f29c', 'assets/70329c1fef56d8a8eb37.png': 'cbfd46c34d4b110a1e4fcbb69375eb4dcb970d35dd994ad8d6cd57e6a6da91d7', 'patient/dogovor/AZ_Contract_Sample_Approval_20261015.pdf': '9d89e03a3137a305199181faaaf115bc48c9ce43d689142da624e29fcd7fa356', 'assets/30299ba903fd9c7517b3.svg': '56b0d23f22779f58462a2d639983c9872fd25281547a7fcff8b83fecefdf3cd5', 'assets/15e2d44f0157a515db97.svg': 'fbbcd0ecb6b712770fefb1a7c1148662c71eaadbacb118da0805a050a020a15a', 'assets/6ee884df24e48ba85fdd.svg': '834198bf9e56cedf3e8d35fd3346bb6051c2ad33fb981dc2922a9d0a29d600a5', 'assets/b060acc3dfeea870ab98.svg': 'bacb9ce503c878b6f001e2ec319531d90f4c22f5b6c8ac5ab95c64bcf1878848', 'assets/2fa5dbdfb4f891fa7d4a.svg': '1aed8ef96c5e9bc929f7987a5880c0717bd7b3331ddf8badafb6aa8a645c89a3', 'assets/8bd7d0a7b055a94ef022.svg': 'b825c938f7432396bb281ce9b5af3e223f7b2d560f4ec9b780b1adfb6ed85981', 'contacts/index.html': 'fabf3273fe71a644df5d827e1c73932f9bc71bacdd012bf683725c27cb56736e', 'cookie/index.html': 'c760353f5b5d1c80c58640b6099562ce8c079e1c7e8669d48c804993baf530d4', 'index.html': '600d2bc8ccc02a893322c5e1a5b39450a45accd2023385fb313d03f0597bfa33', 'kompleks-uslugi/arhitektura-zdorovya/index.html': '8892cddaf963015e2d4bc4cf6d060d74d067d67601fbd1102fb2a839cffa1505', 'kompleks-uslugi/index.html': '181c82e0a840f2c669a2418fd175138669a3e6af58e24af58df6218c6777df8e', 'kompleks-uslugi/korrekciya-vnimaniya-deti/index.html': '5ff5bfee3df751606feb666575f7b0bb8d32f3f3e9c8f42faff8fe172a1cf6d8', 'kompleks-uslugi/zdorovaya-spina/index.html': '7969f4c86ca73922534196980bf932802d5de2fe7dc1eb08f34b97f4ee0fdc3b', 'o_nas/index.html': 'ad48d44eadaa3fea7dbec387b3f28d04136d0010b766beb66ebedfa98c9684e5', 'otziv/index.html': 'dae9dbadf4e47dde45bd710362a36532e08152ba1c6edae44e36b290580e490c', 'patient/dogovor/index.html': '6be73702a53dc21f4f78ccb674438f738376d632c2a790b70e9ef63f5d0eacc0', 'patient/gosgarantii/index.html': '2c93b3eb3ebdaa7956ef422adf9839addde58698cab39b9a3756bd17fc088fc2', 'patient/index.html': 'f5dbb8437f81596c5f179c1e5319eb4302a367c40a4189a5226271618c492ec9', 'patient/licens/index.html': 'a2e66ee0155c3dcb810db71d274ae38e734a68782ebbcbeceaeca68fda385588', 'patient/patient-pravila/index.html': '3c27b8b24796fceae1de2138d12bad8e4c1b894bcfbc252fc2fe60082bee11c6', 'patient/patient-rules/index.html': 'f8aa17dedf8fc6ac4e84fd12d920ce5c4e3e611985194192d2c190d1939d598c', 'patient/priem-patientov/index.html': '2e862feaec378642a679bb54727dd2580b75278d3805b765852b740d9b21f87d', 'patient/supervision/index.html': '7acafd5dba4c4ee4f7893919bf66c47fd8870b34ad457c72e931f9c886819056', 'patient/zhaloba/index.html': '8f9c5de9d865a9f354efced2f3c8c022c360e17ae27386321da274ac7c5dc158', 'politica-site/index.html': '4f045caa1009f89a59d7557b1ce7dd3ef3ba86c724e832579e49160caad8095f', 'politica/index.html': 'f5950dc15209df0993f164687e6cb8d1b871e67c93bef0da3849c4475c1d8144', 'rassrochka/index.html': '398ea1a2ab04512d73d384da502cfd1d27ccffaa97444e329cbaa3ed7d0aadba', 'team/bosak-yana/index.html': '2cac1e5b5bbb2daf038efbd14e907c84c8076b5d3d65c6e73c1bdaa9620a8767', 'team/chirkov-maxim/index.html': '133ac7b96482c72ccfec714ddd5438d5a48ec4b71a69e2117d0fcd38afb7dfcd', 'team/dimitrenko-anna/index.html': '24415803378e5a1d15bcb329edc3da06e7b824ceccda85d6511e1c57c3337806', 'team/golishenkov-vladislav/index.html': '949adfcf28886a24629da60c175bf600ff69c0ed8e9d5f2ea6ca1a76ae6f7592', 'team/holodtseva-valeria/index.html': '3eb74e1eab216d65fdcfa69ca33a780edbf69128ea0f2caf5965c56d8138e6ab', 'team/index.html': 'f33fedfde771b075c7181adddadb00a3adf32e49e7cbcb1ad78b38a6cee15d7f', 'team/lapina-natalia/index.html': '4a043baf83ee7fe497466c90ff53b56779fe832e4e4ce9e15f89bac55c754e2d', 'team/lubaeva-alena/index.html': '0f041c7c96efd911bbf7c69254dc374065910449d33fddb4db7041d2c06b7172', 'team/mamontova-lubava/index.html': 'd66c0f39a7b0f1c5740f1c67ca109d17ff1f5a99218dabe0b3507655727a79c5', 'team/movseyan-babken/index.html': '6c3fd0efc5175bc18050d74cc1c87b15b64b440dc3a3b5cdd87bfb44827129ec', 'team/nazirov-uktamjon/index.html': '7ad6d72f55e825b191621f4c1cdd048659943ffc7092c63c971e1c60fd81cc33', 'team/pavlova-anna/index.html': '858ac0f2ed6ce748de522599877cc8594d2eb922dc10f8c060abbc5153e7a95d', 'team/rumyantseva-maria/index.html': '7c509a2bd2a4631996f4b4b1594ce398f6c59d093726352f2a12adcae1d4c54e', 'team/starostenko-vadim/index.html': '29de9bb3c078ad30d1d356f7cfe201a447c422568f3262cf61f7694f0b0ff408', 'team/zubachev-roman/index.html': 'b069dc46613be5dc6d41d843db1adc2153f45118ed0b927fef873a7527c1a681', 'thank/index.html': 'da6fdd5003cc17bc37fce2595a56afebe94eacc72a3b38d31ff5253791cb92be', 'uslugi/craniodontic/index.html': 'acbafffad6be055c1dae333017ee4efa4dbfb9509bd5aa5bd580fb4a41e7ce23', 'uslugi/diers-diagnostica/index.html': '14127566e4bc13c69cb8e8ac0938e4dc976d3acfcbc2b17fd35df0381e609eb7', 'uslugi/diers_research/index.html': 'f4c3d0c6f3ced4196b7da2d187def060c3fb3b1e0bb3d4dde273792f0f67b22c', 'uslugi/doc_educate/index.html': 'a287b63e0136355c429414ab49b05c54b473ad977e60e6e21cea55a949f7c238', 'uslugi/functional_dentistry/index.html': 'c943a7fddd2ce7c1e1f63b5815bb2ef1ddcfcae58a012eceaa397a50e000a77e', 'uslugi/gastroenterologiya/index.html': '33968bca6a1ad6ab71404795b47a9b76be8901151b1e31a8684d7c367ea7f831', 'uslugi/hygiene_prevention/otbelivanie_zoom4/index.html': '46f3deaef969d1093b2549213b3f4187489b5dfc34e06759ff3cdac2b0da01ef', 'uslugi/iggt-terapiya/index.html': '980ba61133a68ac8641249830484509cc8c4b87365d9ee90387e45d94753d28f', 'uslugi/khirurgiya/implantaciya/index.html': 'd13bc6ab9579e61fd40b6f6dc44d65eda268d327eec99d9828f6a8c1089fd460', 'uslugi/konsultatiya-stomatolog/index.html': '5d24f29d87e75cbf8fbf13c0f92b62493ce8c2b1049bbd4e0ab814590618c9ba', 'uslugi/lechenie-zubov/esteticheskaya_restavraciya/index.html': '3676b83d500e4f310a262d6dae545228ef040a3acf239fe319a9dbfd1bd7ad2a', 'uslugi/lechenie-zubov/karies/index.html': '73089ae007ccd3eed7fee214b7c8e7ba541005fb04e03980c515d532191342d1', 'uslugi/lechenie-zubov/kista/index.html': '08a8646f1b7ef333ab1c5e2f3f5d40fdef045fd91d399fe3155650d4e5e0ca74', 'uslugi/lechenie-zubov/periodontit/index.html': 'c5eb0d921b470d57435ce30c03bd72c2ebd78cce0c04de923f3c6187548ff6bb', 'uslugi/lechenie-zubov/pulpit/index.html': '7c5377f2c04d5e1d9484ba5e7139a760425aaf1702314a128110b822a898b438', 'uslugi/lechenie-zubov/udalenie_zubnogo_kamnya/index.html': 'a0d06a6b854efb8f094a8fa1a83aaca03f4e5783b44d6aa143e47cc3cdfe0f67', 'uslugi/lechenie-zubov/zuby-mudrosti/index.html': '9ab83357a86a6cca12cf1117390db5767305d49058c41eee91c98f7132b0bd3c', 'uslugi/miofascialnaya-terapia/index.html': '3ba313421db95e0ddff1fbb3b89f3c77a9421586b553540ed2eafaa8fce7b341', 'uslugi/miofunkcionalnaya-terapiya/index.html': '7345bf0108eb6fc5d6cab42d056ad3717c88f9d9550a01a625ea67b4b87318f3', 'uslugi/neyropsihologia/index.html': 'cf371b7bea38f1764f30cc6e4ac34540db8b71ec4baa252cdab74dda912d029f', 'uslugi/nutriciologiya/index.html': '8a2e54dd5963036ec0ea6ced51b7bf4d0cdaf8cf764096da39d04cb63fae7496', 'uslugi/orthodontics/index.html': '145496b1239b20e45f3054aa713d592f497b92d73851870602831db380dd052f', 'uslugi/ortopedicheskie-stelki/index.html': '7e28a529c76024e395a575ffe93f213a4c0093977a12fdac8f53c826add5fd0c', 'uslugi/osteopatiya/index.html': 'b25b1051ce51300e066033ad2ca17bc655a655dd9c9d48d0097b21910d72c07d', 'uslugi/preventivnaya-medicina/index.html': '97040d39762f205f2041e46fc023eb3fcf129b67c956bdfaf36901253a961242', 'uslugi/prosthetics/index.html': 'e130a00989af70801a6c02bc94b16e3c88c021b17ca2a3fd896c782d9a303b13', 'uslugi/prosthetics/koronki/index.html': '003b20068edca112e1b81e35b1b218c0e9470accae2ae57539b3ceb2bd1f00b4', 'uslugi/prosthetics/koronki/keramicheskie/index.html': 'b50e8cb1f30bc1d94d3eff215e052cc97fd6220c8a71993a0642235ae0d70812', 'uslugi/prosthetics/koronki/kompozitnye/index.html': '2c314555683061474509f543d1301b270627fababf7da5b55f471d644aa61f28', 'uslugi/prosthetics/mostovidnyy-protez/index.html': '5e39d69ced23d5c33c39c26750f3303350c102d2de1a7aff5ed2691fbd485af2', 'uslugi/prosthetics/viniry/index.html': 'dbb48eb05b9c39b9105402e7e675aefc1daea50efde2d62fbe85215a9d6be832', 'uslugi/terapia/index.html': 'f335689d8af73beed2f473eee56341eb3b7c539e5804908ca2e6a42744a4790d', 'uslygi/index.html': '17544ca552e9d39478354064140976fd5a3692bd9e62c96a6cc616cfc700973f', 'uslugi/lechenie-zubov/index.html': '615f8fb23f0e7f0e316b640100b811f0bf9675cf0ad89a087987d2a146d7caf1', 'uslugi/hygiene_prevention/index.html': 'f49f22612fd9d289c0dfdf5d8eca2101994eb373036abfc43b760d89dd91a109', 'uslugi/khirurgiya/index.html': '78679f39cb5db408785c526dbda83fdee16862596fae3c02482b1eadec310e84', 'lab/index.html': 'd6a5f36d3dcd334cf1cf97e6e123a2a513ef2d339e36fba1efae8d78dc705c03'}, 'absent': ['team/tproduct/834140634503-kazantsev-leonid-evgenevich/index.html', 'team/tproduct/338106745633-palunin-vladislav-andreevich/index.html', 'team/tproduct/392688125713-mozharova-ekaterina-sergeevna/index.html'], 'redirects': ['/team/tproduct/834140634503-kazantsev-leonid-evgenevich', '/team/tproduct/834140634503-kazantsev-leonid-evgenevich/', '/team/tproduct/834140634503-kazantsev-leonid-evgenevich/index.html', '/team/tproduct/338106745633-palunin-vladislav-andreevich', '/team/tproduct/338106745633-palunin-vladislav-andreevich/', '/team/tproduct/338106745633-palunin-vladislav-andreevich/index.html', '/team/tproduct/392688125713-mozharova-ekaterina-sergeevna', '/team/tproduct/392688125713-mozharova-ekaterina-sergeevna/', '/team/tproduct/392688125713-mozharova-ekaterina-sergeevna/index.html']}
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
 for name in MANIFEST['absent']:
  require(not (root/name).exists() and not (root/name).is_symlink(),'Удалённый профиль снова появился: '+name)
 return result
def atomic(p,data,st):
 fd,tmp=tempfile.mkstemp(prefix='.az-massage-wording-',dir=p.parent)
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
  if name.endswith('.pdf'):
   types=[line.split(':',1)[1].strip().split(';')[0].lower() for line in h.read_text().splitlines() if line.lower().startswith('content-type:')]
   require(types==['application/pdf'],'Incorrect PDF Content-Type: '+path)
  if name.endswith('.html') and name!='thank/index.html':
   for line in h.read_text().splitlines():
    if line.lower().startswith('x-robots-tag:'):require('noindex' not in line.lower() and 'none' not in line.lower(),'X-Robots-Tag blocks indexing: '+path)
def verify_redirect(path):
 with tempfile.TemporaryDirectory() as td:
  h=Path(td)/'headers';b=Path(td)/'body'
  r=subprocess.run(['curl','--noproxy','*','--resolve','azmednsk.ru:443:127.0.0.1','--connect-timeout','5','--max-time','20','-sS','-w','%{http_code}','-D',str(h),'-o',str(b),'https://azmednsk.ru'+path],capture_output=True)
  require(r.returncode==0 and r.stdout.strip()==b'301','Прежнее перенаправление изменилось: '+path)
  locations=[line.split(':',1)[1].strip() for line in h.read_text().splitlines() if line.lower().startswith('location:')]
  require(locations==['https://azmednsk.ru/team/'],'Неверный адрес перенаправления: '+path)
def verify_all(prepared,verify,redirect_verify):
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
  list(pool.map(lambda item:verify(item[0],item[1][1]),prepared.items()))
  list(pool.map(redirect_verify,MANIFEST['redirects']))
def apply(root,backups,verify=verify_http,redirect_verify=verify_redirect):
 prepared=prepare(root)
 protected={n:(root/n).read_bytes() for n in (*PHOTOS,*MANIFEST['protected'])}
 changes={n:v for n,v in prepared.items() if v[0]!=v[1]}
 if not changes:
  verify_all(prepared,verify,redirect_verify);print('ALREADY_INSTALLED=PASS; HTTPS=PASS');return
 backup=backups/('massage-wording-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700)
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
  verify_all(prepared,verify,redirect_verify)
  for n,b in protected.items():require((root/n).read_bytes()==b,'Protected content changed: '+n)
  for n in MANIFEST['absent']:require(not (root/n).exists() and not (root/n).is_symlink(),'Removed profile reappeared: '+n)
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
 print('MASSAGE_WORDING=PASS; HTTPS=PASS; PHOTOS_AND_PRICES=UNCHANGED')
 print('APPROVED_REPLACEMENTS=5; TESTIMONIALS=UNCHANGED; HTML_UPDATED=1')
 print('LAYOUT_AND_LINKS=UNCHANGED; COMPLAINT_FORM=CLOSED')
 print('DOCUMENTS_CONTACTS_AND_CANONICAL=UNCHANGED')
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
