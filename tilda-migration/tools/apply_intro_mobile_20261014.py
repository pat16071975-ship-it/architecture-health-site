#!/usr/bin/env python3
"""Approved mobile introductory layouts on four pages. No network download or Git checkout during apply."""
import argparse,concurrent.futures,fcntl,hashlib,json,os,shutil,socket,subprocess,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
MANIFEST = {'version': 1, 'files': {'o_nas/index.html': {'before': '2ede9bc2dcd5ce5c1ea2de495330a3c6d3919724d06afbb7e7ffa15a905b3764', 'after': 'd6eec1509bb8a39bb150b386c47d2520453958f3c36c0ef806e825e3efead28a', 'replacements': [['</head>', '<style id="az-intro-mobile-20261014">\n/* Keep introductory content in natural flow within the mobile viewport. */\n@media (max-width:639px){\n #layout-2501566971{display:grid;grid-template-columns:18px minmax(0,1fr);\n  column-gap:10px;row-gap:16px;width:min(460px,calc(100% - 30px));\n  height:auto;margin:0 auto;padding:0 0 24px}\n #layout-2501566971 > .visual{position:relative;inset:auto;min-width:0;max-width:100%;margin:0}\n #layout-2501566971 > .visual-text{width:100%;height:auto}\n #layout-2501566971 > .visual-text > .visual-inner{height:auto;align-content:normal}\n #visual-2501566971-1623210245152{grid-column:1 / -1;grid-row:1}\n #layout-2501566971 > #visual-2501566971-1623209996701{grid-column:1 / -1;grid-row:4;width:100%;height:auto;aspect-ratio:3 / 2}\n #visual-2501566971-1784967907854000002{grid-column:1;grid-row:2;align-self:center}\n #visual-2501566971-1623211118827{grid-column:2;grid-row:2;align-self:center}\n #visual-2501566971-1784967917463000004{grid-column:1;grid-row:3;align-self:center}\n #visual-2501566971-1623210537934{grid-column:2;grid-row:3;align-self:center}\n}\n</style>\n</head>']]}, 'uslugi/functional_dentistry/index.html': {'before': '4415316d712ca15b99ad0edadd8bcba0a9306e444acba3a1ee939c23b2c49a47', 'after': '4e7db77e8d7c13850dac24c3f46cbff088acd509efe54d7ce6bef29a78afb56f', 'replacements': [['</head>', '<style id="az-intro-mobile-20261014">\n/* Keep introductory content in natural flow within the mobile viewport. */\n@media (max-width:639px){\n #layout-2500810431{display:flex;flex-direction:column;gap:16px;\n  width:min(480px,calc(100% - 30px));height:auto;\n  margin:0 auto 24px;padding:14px 12px 20px}\n #layout-2500810431 > .visual{position:relative;inset:auto;flex:none;min-width:0;max-width:100%;margin:0}\n #layout-2500810431 > #visual-2500810431-1784969828861000001{position:absolute;inset:0;width:100%;height:100%;z-index:0;pointer-events:none}\n #layout-2500810431 > .visual-text{width:100%;height:auto}\n #layout-2500810431 > .visual-text > .visual-inner{height:auto;align-content:normal}\n #layout-2500810431 > #visual-2500810431-1623209996701{align-self:center;height:auto;aspect-ratio:220 / 209}\n #layout-2500810431 > .visual-button{align-self:flex-start;height:auto}\n #layout-2500810431 > .visual-button > .visual-inner{height:auto;min-height:44px;padding:10px 12px;white-space:normal}\n}\n</style>\n</head>']]}, 'uslugi/osteopatiya/index.html': {'before': 'b45b4e71eee40834af3f10c7255c1dffccf53573d2ad5db892e9835f600989e1', 'after': 'fa62cc07794d9a3a283fda314733e7ba3623ffdf9fc2c1026fa7162e45a6e6cf', 'replacements': [['</head>', '<style id="az-intro-mobile-20261014">\n/* Keep introductory content in natural flow within the mobile viewport. */\n@media (max-width:639px){\n #layout-2788165601{display:flex;flex-direction:column;gap:16px;\n  width:min(480px,calc(100% - 30px));height:auto;\n  margin:0 auto 24px;padding:14px 12px 20px}\n #layout-2788165601 > .visual{position:relative;inset:auto;flex:none;min-width:0;max-width:100%;margin:0}\n #layout-2788165601 > #visual-2788165601-1784969828861000001{position:absolute;inset:0;width:100%;height:100%;z-index:0;pointer-events:none}\n #layout-2788165601 > .visual-text{width:100%;height:auto}\n #layout-2788165601 > .visual-text > .visual-inner{height:auto;align-content:normal}\n #layout-2788165601 > #visual-2788165601-1623209996701{align-self:center;height:auto;aspect-ratio:298 / 199}\n #layout-2788165601 > .visual-button{align-self:flex-start;height:auto}\n #layout-2788165601 > .visual-button > .visual-inner{height:auto;min-height:44px;padding:10px 12px;white-space:normal}\n}\n</style>\n</head>']]}, 'uslugi/miofascialnaya-terapia/index.html': {'before': 'eb1c7cbecb4427d9d90ca508a5df69eeca97983e37f85743667a355c6d79e637', 'after': 'a84c15a5c2e4d1f2fbb3bf46acab29b91b4b53a3df6ce1ea12a2c77328ca9930', 'replacements': [['</head>', '<style id="az-intro-mobile-20261014">\n/* Keep introductory content in natural flow within the mobile viewport. */\n@media (max-width:639px){\n #layout-3710603101{display:flex;flex-direction:column;gap:16px;\n  width:min(480px,calc(100% - 30px));height:auto;\n  margin:0 auto 24px;padding:14px 12px 20px}\n #layout-3710603101 > .visual{position:relative;inset:auto;flex:none;min-width:0;max-width:100%;margin:0}\n #layout-3710603101 > #visual-3710603101-1784969828861000001{position:absolute;inset:0;width:100%;height:100%;z-index:0;pointer-events:none}\n #layout-3710603101 > .visual-text{width:100%;height:auto}\n #layout-3710603101 > .visual-text > .visual-inner{height:auto;align-content:normal}\n #layout-3710603101 > #visual-3710603101-1623209996701{align-self:center;height:auto;aspect-ratio:1 / 1}\n #layout-3710603101 > .visual-button{align-self:flex-start;height:auto}\n #layout-3710603101 > .visual-button > .visual-inner{height:auto;min-height:44px;padding:10px 12px;white-space:normal}\n}\n</style>\n</head>']]}}, 'protected': {'fonts.css': '3d16ea7238b58a5cf0904098ad236aa6be0b3facccf3a53009af08fcceedce6c', 'contacts/index.html': '75be0518b7674637e4b0c813af7748a6f11c56fff89a4dc70d6caeec4722dd74', 'kompleks-uslugi/index.html': 'cfdbcd74a86770266fc3083faaa33649489a7266987941cfce1bc8c21b046436', 'kompleks-uslugi/arhitektura-zdorovya/index.html': 'c3f4356fe59cdd6ab85feee2c43f661e4bdc52944ee97a328766324b4f244223', 'cookie/index.html': 'e6ee1cf7493b7222d9ac221e1b695625afde912d4531ef77638ba01d71ce619a', 'otziv/index.html': '6f06ce3b1696035c93bc8d430dc82b677a63f2079271b512bdb5e801daf4fa0e', 'kompleks-uslugi/korrekciya-vnimaniya-deti/index.html': 'd328ff12da81d0c058e142d707a4c25c8615ca6a1956484df4ffb4264e6fa941', 'lab/index.html': '21adf00d44b0cae5f006197a6b20f57f582cbf6499b08abbea7c44fd4b746fb7', 'patient/dogovor/index.html': 'aebddf8cd0b001a8a84711eb567c183f1c9616214ea05ef2a2789121e72b1c06', 'kompleks-uslugi/zdorovaya-spina/index.html': '379686be85d599f8720022e3c2b437e717631e2f6070adcaa9259d94a89383e4', 'patient/licens/index.html': '0ec024f8984c5b6b42e8a91896c8a244cf403113186ac80cf59d3719a921da3a', 'patient/index.html': '322b2972b62e3053d401acc0a4fa9f157903e6a5b2827caff34a6a69ba6c8968', 'patient/gosgarantii/index.html': '188dcf8869d2b147b25fa90a007f154d809cb319a4f48b9a2f92826c6aeb1339', 'patient/priem-patientov/index.html': 'ce440702925e0f4639b0e52f773602a91e98ea209795773950c5cff5bd9ff25c', 'patient/patient-pravila/index.html': '4d0b51d087d86d4d17a89e7254beaebd66c154e14bfbdccd32f2399d669565b6', 'patient/supervision/index.html': '1adce00392863736aaacb07bdb8711ccd3a8ea405689b5bf78a7e8901683f4d0', 'politica/index.html': 'bfecfdd0f5dfa6d87bf7d611325e924f1922ea8fa77e261e627a3214ea53997d', 'rassrochka/index.html': 'b9872c9b677e1719f35ceb58b4dbccf07e7071ca19a2bb94dbb05525d830471e', 'patient/zhaloba/index.html': '4a5df359445e4bdf3ba1ffc5395ce3dcee6b80a519e2cadfb8c865efa10247b1', 'politica-site/index.html': '8f0e01ebad44b8aaaab0832f6f4a52e235cf0a21e33d809fbc620bc92596124f', 'team/index.html': 'b9c59466944d43b166e0925add8a39a1760d56fcd52be3838cd6f200a255407f', 'team/movseyan-babken/index.html': 'b72b9827d1e7114bd28ce8e8742f7c22d2d886ce8d18448b9640c923113793fd', 'team/rumyantseva-maria/index.html': '522d6212d15a4628596afd82738cdf274545004126ff179c9edd84f75a0c5682', 'team/zubachev-roman/index.html': 'f6e715466e6b50c2d473b0dafbc74cb0131a6812c0b76c54e3a85fb24e93d9cc', 'team/tproduct/834140634503-kazantsev-leonid-evgenevich/index.html': '74b8ffc0ea3582bf3528d3ea81447f2020a97588c068ccee4e3d1701cb89c46a', 'thank/index.html': 'b054ecd5a343b7b559a5b23b5e8321ea7b0f076bea937b54c13b2ee2504aaf3a', 'team/tproduct/338106745633-palunin-vladislav-andreevich/index.html': '3d4cb3d939fea2b6b811eb93885703d0e3f6eccfd8791cbfde70da6edcdca2ff', 'team/tproduct/392688125713-mozharova-ekaterina-sergeevna/index.html': '5137a0f2357ede6a7b69f3f6684fad181b8fb3f77e2deac9286ddedb1712183d', 'uslugi/doc_educate/index.html': 'a13d11d4c6e13ed8ac6f09e361163617a96b553ece79dc3d40a95fea77609f98', 'uslugi/gastroenterologiya/index.html': 'd23a41e947780a6cc560ba4880cc16a510df3c38f97d32b2c73c986f2ec2dbc9', 'uslugi/craniodontic/index.html': 'e9803337ed857e7bf069e4c753d04bf2dd6e12c24f511aec3ad09a3bbf8fd9e4', 'uslugi/diers-diagnostica/index.html': '3d759d0d8638966b88f883ddd7912b05c3415f8de9b1706f20cd3aaf3f2ba4ee', 'uslugi/diers_research/index.html': 'f1e11e0f62aa375807ff22bdcb6a93ac9e51242b6a22f35606d72300b1420f63', 'uslugi/hygiene_prevention/index.html': '645d451ecee27ad076a7b55fdc34c39c162553c9190ea4ff4d0adeabc86325f6', 'uslugi/khirurgiya/index.html': 'db53a532b9e40b49841c3c37c4f8a8fdd7c4f8452372a94f3573004241c0207b', 'uslugi/khirurgiya/implantaciya/index.html': '472978b1f9919c45be191aa43dc11df100bd305a379ad8af9383fe4700800307', 'uslugi/iggt-terapiya/index.html': '6c5875268bdf6f5f2223366f1c15380545b6c550f89baf4b0aaa5344a3103e32', 'uslugi/konsultatiya-stomatolog/index.html': 'abd5d8c581f8fb71d3b328f4a18477d7ed6a8fe1a315a4f960c64e43f93d18c8', 'uslugi/hygiene_prevention/otbelivanie_zoom4/index.html': '1d6507bc370ae6888720cf69a775ee334c176d84dca1fc3aad80cac2f5e6c232', 'uslugi/lechenie-zubov/pulpit/index.html': '1b6db89d18624485fa79e000ed80c38b0e8df44615cde0b71baf50fb9df74878', 'uslugi/lechenie-zubov/kista/index.html': 'd4368886c7261017055f2cfcbfe2d2383d71674ca0ee8cb32034d6f59870186e', 'uslugi/lechenie-zubov/esteticheskaya_restavraciya/index.html': 'ce4fd316e5ef26b031994b6c52beabf4af21171ae0b43015f258cdf6347bb31d', 'uslugi/lechenie-zubov/karies/index.html': 'd40cda4688f54bdcd2d0fdcf77bfd32200ef37ab64ae0246a3f6ff52baed046f', 'uslugi/lechenie-zubov/periodontit/index.html': '7deb8604d9afa80e069c7c1b2474d4a7452516e38f49575a6c9b82017132da19', 'uslugi/lechenie-zubov/index.html': 'e53701d20feaea14df9f53ed2dc34f315b4d84a0db1d298b5b03d883b0bb8982', 'uslugi/lechenie-zubov/udalenie_zubnogo_kamnya/index.html': 'e69c355d80154b505e844b091106112e193473dc956fbce5e22caaf13db4ebe7', 'uslugi/lechenie-zubov/zuby-mudrosti/index.html': '9981cd96f7d30a45d8fa4e7bd24d272bcdec76171e67ee907853b9fd88db5dc3', 'uslugi/miofascialnyy-massazh/index.html': '00e3beeba83b003ccd3151b7bf90e36415e37edf0f805947e73516645a81a41d', 'uslugi/miofunkcionalnaya-terapiya/index.html': 'c5b4599a25b4109d26b9cdef994dcd4295765bf721b7c3da01cd24f45d9ebb1d', 'uslugi/neyropsihologia/index.html': '8accda9c46845671d8323f91a3d38c79b71d6c1d7a8186c928c4adaed411c63c', 'uslugi/nutriciologiya/index.html': '68f6b99398c61df0e9b2ead8ca6f79f682c58b3e563f4324c7a577c7ae0b95a5', 'uslugi/prosthetics/index.html': '4abd6bdeb0ccc4fbf63440e2817cd7461ef2a796b9264eec09f2ecea9ec67c39', 'uslugi/ortopedicheskie-stelki/index.html': '2006c3df04c3e54ac3656dc593e84219ab32d16d4f128053c9f70e265347612d', 'uslugi/orthodontics/index.html': '196715a3340f9351e0b9d008c4670bdbd743b40e5ad6b168993cf8ece2d4d827', 'uslugi/preventivnaya-medicina/index.html': 'c93c466d9e44b64488de4ca69ffb034076c40af1bfcf953208c510abed770f20', 'uslugi/prosthetics/koronki/index.html': '55d91c3c2ab3842a704fd645e6b5a5909b48b84048e0e9553213750e00585028', 'uslugi/terapia/index.html': 'dacf943ff38505417ea571458cfc5abb9a619f3109921e8b56e23e73f4bb1995', 'uslugi/prosthetics/koronki/keramicheskie/index.html': 'b8bbf5ad616f1ee4d7c3a0a69362009242d2c2aa9dd69b7bdfef6898f29d2d4c', 'uslugi/prosthetics/mostovidnyy-protez/index.html': '44172636847b60518360cca67bbe2e7490223754c3624134f2010d501b93ad26', 'uslugi/prosthetics/viniry/index.html': '3c3cbebe960adc0c7ad19f0e5ae345fa4727e699d0cd5bdd9a27b581e05d6824', 'uslugi/prosthetics/koronki/kompozitnye/index.html': '9a4b0f1c1b477acea7d6eeb34ed4157bb0fd9e302e5420ecacd644ee68f17260', 'robots.txt': '9a93da66b2f7b9bc05ad7429409692dff442b3bf8f988f2937ae21ad9cf6744f', 'site.js': '190dba2936bc2def347a43e94b81e6fe226ee77ae38220db9e4790a17c355361', 'uslygi/index.html': '8a8e024327b1cb113317697894df49df2bb1f6eaf8120d2c279977a34a40c867', 'sitemap.xml': '74ffc28b998df2bc8425c17f6b3369cf15484b9398a17dc48aef497dd87b8ba4', 'site.css': 'bb0e025c9e291ccdb24da8b9e80cfac3c85d998c1387aed24fa645cfa0fe7320', '404.html': '3fbe5b62b98d38eb325d3b99d47a42fd9d90fdda29ba4ae2002ceab128cab21d', 'assets/az-doctor-cards-20261013.css': '5813a1e65ce1ececfddb2d11dc6669cff3f61e16390b1b01cf870a512b3e62a0', 'assets/az-doctor-cards-wrap-20261013.css': 'b3504521129c394bb669fbc4e420ed6e86c4ed9193b1390a1ee3315dadeeec9a', 'assets/az-mozharova-approved-20261013.jpg': '621542c5ea7b609752813c546548d270d4f1848370eb1901387d48882fa98e4c', 'layouts.css': '66d78398182263a458236c439c1a098fc8390539c7d45511d9240bd54f1a3a71', 'assets/4d67d8e3617de3463719.jpg': '173ab0e4c1ad9b2f86af7545077bdcf1bde17ec73b5d46ad46253cfc3559928f', 'assets/ae539bb9ce3fbc525200.png': '79fb247028b4667e1da2316b9618a512e482bab6ea917507162658cae913f464', 'index.html': '10429335abc2b1f1fe81187b0f5b67f1b622539cc53232a39d0782eb0f4fd6cd', 'team/pavlova-anna/index.html': '86733a7861aba315f95c66fa61466b969ecdecb5ca080d61e606259033fb773b', 'team/starostenko-vadim/index.html': '2906af85d6f37e1e083060f730974bdb9722457edbe279725fd4566ce3fe2db5', 'team/dimitrenko-anna/index.html': '7edec7272fc2e871565fbadcc7000737f83a66e8965b563e21c0f2e2a689d9dc', 'team/mamontova-lubava/index.html': '6da8cd07f9210bd52b4c56c1e308f44fac77830951dda680aebb4fac39e4def6', 'team/chirkov-maxim/index.html': 'ea2613fb0d65db9c7266dc123c583c1e204cbff3d021ff63532bf2ba7011259d', 'team/bosak-yana/index.html': '4cfb9705068280cdc542fad447416c52cb8c68c8f1052da5feab549cbbbe3a82', 'team/nazirov-uktamjon/index.html': 'b233fb0d6e3f08cbe2d166fca0b59d9d507cd4524519dc20b27eb01cb80c4d25', 'team/golishenkov-vladislav/index.html': '98629815136951f88cc8ae1dd6880c520d28a54abb7383405779f90974731ea8', 'team/lapina-natalia/index.html': '4b57adfb93c695c1ac1d0d60e120e9a1f4b3d4dde33119b162f22600b3ff29aa', 'team/holodtseva-valeria/index.html': '827ef57f1524dba8bc85401573da9b671f663eb29acc176cc37dfedcd7791ad7', 'team/lubaeva-alena/index.html': '43655413799be3234fcbc5815b7711304d919d7ac09933545c7adba3219b192a', 'patient/patient-rules/index.html': '3db34d9c51700666860820e14bb5521bdbf1545f8fa03dfeb714e17b89b3d552'}}
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
   for a,b in rule.get('replacements',[]):
    require(s.count(a)==1,'Неоднозначная замена: '+name);s=s.replace(a,b,1)
   s+=rule.get('append','');new=s.encode()
  require(sha(new)==rule['after'],'Неверный результат: '+name)
  result[name]=(old,new,p.stat() if old is not None else None)
 for name,digest in MANIFEST['protected'].items():require(sha((root/name).read_bytes())==digest,'Изменена служебная страница: '+name)
 return result

def atomic(p,data,st):
 fd,tmp=tempfile.mkstemp(prefix='.az-intro-mobile-',dir=p.parent)
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
 backup=backups/('intro-mobile-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700)
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
 print('MOBILE_INTRO_BLOCKS=PASS; HTTPS=PASS; PHOTOS_AND_PRICES=UNCHANGED')
 print('HTML_UPDATED=4; MOBILE_INTRO_BLOCKS=4; NATURAL_HEIGHT=ENABLED')
 print('TEXT_AND_FONTS=UNCHANGED; DESKTOP=UNCHANGED; CANONICAL=UNCHANGED')
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
