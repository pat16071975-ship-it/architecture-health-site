#!/usr/bin/env python3
"""Fix the oversized lab document icons with exact guards and rollback."""
import argparse,concurrent.futures,fcntl,hashlib,json,os,shutil,socket,subprocess,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
MANIFEST = {'version': 1, 'purpose': 'Constrain existing lab PDF and pen icons beside their existing links', 'files': {'lab/index.html': {'before': 'c26e75a7d2093fa837db8ea73a49b783e3ae131a8894238c4810d309f014ff10', 'after': 'b02d92f6912001c2b3cf336dab5d8bfe0032cf884e1468daab7eeafed93822e9', 'replacements': [('<div class="prose"><a href="https://drive.google.com/file/d/1n8JdBaPkoOTTY9S1BVJMh_h84oPgJMAS/view" target="_blank" rel="noopener" style="color:#2080c4;text-decoration:underline;text-underline-offset:3px">Техническое задание для изготовления аппарата</a></div><div class="prose"><a href="#layout-2771512401" style="color:#2080c4;text-decoration:underline;text-underline-offset:3px">Запросить прайс-лист</a></div><figure><img src="/assets/c2fbd2ba58a9350a5364.svg" alt="" loading="lazy"></figure><figure><img src="/assets/d13db62171f56458b979.svg" alt="" loading="lazy"></figure>', '<div class="prose"><a class="az-lab-document-link" href="https://drive.google.com/file/d/1n8JdBaPkoOTTY9S1BVJMh_h84oPgJMAS/view" target="_blank" rel="noopener" style="color:#2080c4;text-decoration:underline;text-underline-offset:3px;display:inline-flex;align-items:center;gap:10px;max-width:100%;min-height:44px;padding:4px 0;line-height:1.4;overflow-wrap:anywhere"><img src="/assets/c2fbd2ba58a9350a5364.svg" alt="" width="28" height="28" aria-hidden="true" style="display:inline-block;width:28px;height:28px;max-width:28px;object-fit:contain;flex:0 0 28px">Техническое задание для изготовления аппарата</a></div><div class="prose"><a class="az-lab-document-link" href="#layout-2771512401" style="color:#2080c4;text-decoration:underline;text-underline-offset:3px;display:inline-flex;align-items:center;gap:10px;max-width:100%;min-height:44px;padding:4px 0;line-height:1.4;overflow-wrap:anywhere"><img src="/assets/d13db62171f56458b979.svg" alt="" width="28" height="28" aria-hidden="true" style="display:inline-block;width:28px;height:28px;max-width:28px;object-fit:contain;flex:0 0 28px">Запросить прайс-лист</a></div>', 1)]}}, 'protected': {'sitemap.xml': '7d25e8f24835ef0772c360356f8ee9c350921cb602b155d836713e00b05b40a2', 'fonts.css': '3d16ea7238b58a5cf0904098ad236aa6be0b3facccf3a53009af08fcceedce6c', 'robots.txt': '9a93da66b2f7b9bc05ad7429409692dff442b3bf8f988f2937ae21ad9cf6744f', 'site.js': '190dba2936bc2def347a43e94b81e6fe226ee77ae38220db9e4790a17c355361', '404.html': '3fbe5b62b98d38eb325d3b99d47a42fd9d90fdda29ba4ae2002ceab128cab21d', 'assets/az-doctor-cards-20261013.css': '5813a1e65ce1ececfddb2d11dc6669cff3f61e16390b1b01cf870a512b3e62a0', 'assets/az-doctor-cards-wrap-20261013.css': 'b3504521129c394bb669fbc4e420ed6e86c4ed9193b1390a1ee3315dadeeec9a', 'assets/az-mozharova-approved-20261013.jpg': '621542c5ea7b609752813c546548d270d4f1848370eb1901387d48882fa98e4c', 'layouts.css': '66d78398182263a458236c439c1a098fc8390539c7d45511d9240bd54f1a3a71', 'assets/4d67d8e3617de3463719.jpg': '173ab0e4c1ad9b2f86af7545077bdcf1bde17ec73b5d46ad46253cfc3559928f', 'assets/ae539bb9ce3fbc525200.png': '79fb247028b4667e1da2316b9618a512e482bab6ea917507162658cae913f464', 'site.css': '8b2adf1b395ce968f1f116fac27b9a559d7d42265d1686737ec21dc4590406c9', 'assets/2eb303492fc4afc1e2bc.png': 'fad27a52d50d0e7bb74f40ca28841adf1e3525421d111f197afd5599fc03f29c', 'assets/70329c1fef56d8a8eb37.png': 'cbfd46c34d4b110a1e4fcbb69375eb4dcb970d35dd994ad8d6cd57e6a6da91d7', 'patient/dogovor/AZ_Contract_Sample_Approval_20261015.pdf': '9d89e03a3137a305199181faaaf115bc48c9ce43d689142da624e29fcd7fa356', 'assets/30299ba903fd9c7517b3.svg': '56b0d23f22779f58462a2d639983c9872fd25281547a7fcff8b83fecefdf3cd5', 'assets/15e2d44f0157a515db97.svg': 'fbbcd0ecb6b712770fefb1a7c1148662c71eaadbacb118da0805a050a020a15a', 'assets/6ee884df24e48ba85fdd.svg': '834198bf9e56cedf3e8d35fd3346bb6051c2ad33fb981dc2922a9d0a29d600a5', 'assets/b060acc3dfeea870ab98.svg': 'bacb9ce503c878b6f001e2ec319531d90f4c22f5b6c8ac5ab95c64bcf1878848', 'assets/2fa5dbdfb4f891fa7d4a.svg': '1aed8ef96c5e9bc929f7987a5880c0717bd7b3331ddf8badafb6aa8a645c89a3', 'assets/8bd7d0a7b055a94ef022.svg': 'b825c938f7432396bb281ce9b5af3e223f7b2d560f4ec9b780b1adfb6ed85981', 'assets/66c2653085791a4fa9d8.png': '5a4a6d9213d1f25c32c0306e28aad0702ccdea1e58dc32d3d76323b10c9c50c9', 'assets/10d1def5a0107a7e496b.png': '756f561dca7841ccd2d31f97dc90ad56e98afd5d0950406c49762b2b1f811e9a', 'assets/65e14760056ea4acc6f9.png': '17d3fd58eb917a60421e16ce32012db2b9004efd53de982aae8e85220aa50e28', 'assets/32ed8370d11f1baeceaa.png': 'fbaa72f02f3e6733d398196c2560827c78c309c024ec7badbc144d6bb4c0d157', 'assets/b8e7a530af57ecf75bc2.png': '008c42674dddec10eaabb6e052b736abde7a4cbef68813b8e8ad3990eb939b6e', 'assets/2116a70379b63013e71b.png': '0c4fb243f8fa53d7c82f409d946078c4e9d7ab86c837faef766a03c195b4e133', 'assets/66c2653085791a4fa9d8-lossless-20261018.webp': '6aa6704b9f0057db4d6c2bbfee693ab1eb4b879222ebe8f964eb5d2e70ece720', 'assets/10d1def5a0107a7e496b-lossless-20261018.webp': '926b3acbbc996cd54f5d563e1c0930f9dace598ab97d69e5982adaf2dba001e8', 'assets/65e14760056ea4acc6f9-lossless-20261018.webp': 'a9f60d7b30ebe555bf8518d3334792c367268051988ce4e642b1b878f4f78a13', 'assets/32ed8370d11f1baeceaa-lossless-20261018.webp': '9d18476756b3b3bb222fe87da6c5c02a08d7934d173836d05fb2c7d7c8804f56', 'assets/b8e7a530af57ecf75bc2-lossless-20261018.webp': 'b23826746a9908ed32dda432b2e58844e41c94432a03339a3c5296e423d9ae1a', 'assets/2116a70379b63013e71b-lossless-20261018.webp': 'b31ab720bcd3e2a612db38d3cbe0f13c04692a9a01f8c9dd9d1817ffe72b08ec', 'assets/ae539bb9ce3fbc525200-lossless-20261018.webp': '26c161bb8826f5934c7f9873e7b37da1be72723d448fa402bbb95f85a6101ab3', 'assets/a77a625fc72b6b568fd8.png': 'cec69637b64ce2575359edd8820f3775e38519eeafc20aa20832c8107f77effd', 'contacts/index.html': '8291baa2b4f2e67a535a15638d86d153498c96c7b093307bed851898efbc715c', 'cookie/index.html': '77b3d984be4db6e182a4dc17780ca3bae040d977c08505f74b9f7973e6abf833', 'index.html': 'ebb2b6240dffc8f9f522883992bfbdf4ae890990dce40dff2faa80b61912cbf8', 'kompleks-uslugi/arhitektura-zdorovya/index.html': '63c3e1c5c3f13d961684a68f6fa1685f363814635737e556f12b83efd3fd241e', 'kompleks-uslugi/index.html': '897426df18b5590e112b31860d3b51f16907cc58a2a283831383d26c0eaf8cb8', 'kompleks-uslugi/korrekciya-vnimaniya-deti/index.html': 'fad6dba339f07dafb82c1913d98dcb376902ff5d14e85c7e84a3c50d4daab0fc', 'kompleks-uslugi/zdorovaya-spina/index.html': '5a174d880a0533944259993e33f21461c673773088cd8191d0fdd0cfb2b8ce41', 'o_nas/index.html': '434042466ea9fc8731286962d15e1c6e59545ceb2ffbea005eca142869628d9e', 'otziv/index.html': 'c1b865f2a26ca09ec6f6cc245f301a1b9f2e828ff2b7d11f6a6d79ea17a40dfd', 'patient/dogovor/index.html': '847002317ea36b565cb8b23b5a5d8d0238bd98d067bf2c2c73aaef1d6f9b6b35', 'patient/gosgarantii/index.html': 'e6baebaab46aa849a3261bfbe870fccef93db14bdf1c4e54c57b352888278003', 'patient/index.html': 'dac4ffafc4fafbf9a60f4e65108e3bfe03d2c74bb8b8bee30641b9ec2a1f54d4', 'patient/licens/index.html': '6ded356d614678c7655ba86e1c95f0f24af194d7478112207939f3c48e8a6ced', 'patient/patient-pravila/index.html': 'baf8e8e708cf13aafb8ff11876b761811b219098257c93382cb6732e6271a839', 'patient/patient-rules/index.html': 'e1aec3bdd4b1af7c65b7bcb4e742e89662f016b529263ace4259474f529eb064', 'patient/priem-patientov/index.html': '06758bac2905503790308ebfb64e2a4a8779bcf1bb983620c3c9a55f72eddbaf', 'patient/supervision/index.html': '246bc4d01b08ac7ea601f688fd93d13990d864a521ece34e8beddc3d5362df7e', 'patient/zhaloba/index.html': '230cf94cc6788858e962974d98531df6ddd1d87f68d94bac1aa8dc89acfb4b0f', 'politica/index.html': '230d4472dde904b5c0e59b74832ccf8c3d8826261721ccca56c61ea9782d0982', 'politica-site/index.html': '3f1b38fa00a9f3ba6d733bab01345b4f71a7dfe46f288295f78d237b3c2cfcaa', 'rassrochka/index.html': '085575ab217a751412c928e2b6c42ad132cddff8781334e46946fe5d60c68078', 'team/bosak-yana/index.html': '0e4b643f028404e134bf8c42c596f1d1d02a0b87fb4e844d907d31c1d02e367d', 'team/chirkov-maxim/index.html': '529c5838f2ede42c4000c15cdacc5e103c468e3749755b4bbaf096227f725f85', 'team/dimitrenko-anna/index.html': '05709387383c4768aecd2d385420ff891eb48e8924bb003816e71924d0fd0f4a', 'team/golishenkov-vladislav/index.html': '52db58b389497ba59a2762828686924cf96dbfbeabff401adc6d87ac0a8ab129', 'team/holodtseva-valeria/index.html': '995a2c7a5f587f652c243e822b9e8a88e8021518091ddf8fa2650818d9ebe5bb', 'team/index.html': '5750a7afd79bca21ff4772f6db9b80157588e4f8fef9a3b34be7d75f54113ad0', 'team/lapina-natalia/index.html': '266f8bf33a8ed0cc5c2d51134a5c76f6fcd480d9bc24905643d386e41fd704e2', 'team/lubaeva-alena/index.html': 'b9f6d239d97246701a6be4600d7bea5e050ccc1ab7141a7bb8590cef23f7a0fd', 'team/mamontova-lubava/index.html': '77cde9d472051512a26d267576156d5ed0c7047a0e1ab153d1638f82ab9863cc', 'team/movseyan-babken/index.html': 'e89378398d22997f7fb6e864b614f663d572149aee2c5288d5f5070fdf30d2c3', 'team/nazirov-uktamjon/index.html': '52b095a6c9f35bc9d130dcdeed1d3e544b7d62914e0740b3d90c2e117852a789', 'team/pavlova-anna/index.html': 'c45d5c9ea4c6e6899445d667db70f49b8745fd391c40eb5b820c1f9c4a3d4702', 'team/rumyantseva-maria/index.html': '8a4342734bb6048d020aeba3b6bb5379171b4c553a432f75c2255926bd057c22', 'team/starostenko-vadim/index.html': 'eaff676dcdbbc2436012b10a73bb18e18c9ad56d115ec4c2b9473a3e19df428d', 'team/zubachev-roman/index.html': 'ec99d403ab68401336ebd407114cc605b85f73cd30b2fc4bfeabd97a0871cf5d', 'thank/index.html': '74f01c6f290e54fc611f71cefae1c5682bf9987b05b141664989f1e3c1418d28', 'uslugi/craniodontic/index.html': 'd236b6301b3fc4d340b30547a3186d13c89ba766912d12726a597a93257799cb', 'uslugi/diers-diagnostica/index.html': '3861ca5622482924eab4753d3d9ad2befc017399249376630cb34b85c4359bba', 'uslugi/diers_research/index.html': '9496effbfcde5d8c0760d23ef26ff5a1c60287b696f2259991d4c87f7338cd9b', 'uslugi/doc_educate/index.html': '3e299579cab2a9a674cccb949dab3d5e0c84418fcf3ee511c326c8e385ff8c3a', 'uslugi/functional_dentistry/index.html': 'd05559e374aa76fdfbe4821072bda0b01438c5d4057ffb2a9623326c9ebd1dd2', 'uslugi/gastroenterologiya/index.html': '8c4e15a2c9c91614d7b7dee68500071a8be58e4e82b56608b341a646fa4dd166', 'uslugi/hygiene_prevention/index.html': '11b57a921047c29b3a19471444ddfdcab1851fba44f6c041143273d585dee15d', 'uslugi/hygiene_prevention/otbelivanie_zoom4/index.html': '63596682665523b041a9559afb3ae7056b27c38a9e2c35ae2689bd9042a0f5dd', 'uslugi/iggt-terapiya/index.html': '5220982ef58d751a3b8459659da3972dd53797af9df2ec51505fa2956fe05bee', 'uslugi/khirurgiya/implantaciya/index.html': 'e26bc93718434cdf9f2e440a1d8c9ae0cf101dbc0b5874e8dfe853d9015f1f9c', 'uslugi/khirurgiya/index.html': 'a2cd8d92aa822b16a74a4d3bf23a9defe8bf15242711840d1f51b0b5fa1c2fd4', 'uslugi/konsultatiya-stomatolog/index.html': 'bc4e934c0c73474e14a8e96d976163966d9eeaa1a427f9f82ea2ec7f9cb21ddd', 'uslugi/lechenie-zubov/esteticheskaya_restavraciya/index.html': 'fa303c5818d84f765fded764d9e099af006b8f14f5e146652dce71041d1e69a2', 'uslugi/lechenie-zubov/index.html': '8fa8fe6f2ba53218cbee1c830b521ee445ebff19a10174e790e3e2d62346e8fb', 'uslugi/lechenie-zubov/karies/index.html': '8bf8a5bbd810a5cbeb4ab3d51955f0ab1280c759fb24692775b02d4cc34fafd2', 'uslugi/lechenie-zubov/kista/index.html': '2deea37ac9e79757afb692adb698fb1ca63d00be55fc68a2b9d5da9efebac4c2', 'uslugi/lechenie-zubov/periodontit/index.html': '5812abce78f76300016fcd5b1bfe352b49778da6ed1171499bae709775e500d9', 'uslugi/lechenie-zubov/pulpit/index.html': '43ea42b9ae767a7c5f2e89a26259b57904770c62cecb7c3592bedd691cdb345a', 'uslugi/lechenie-zubov/udalenie_zubnogo_kamnya/index.html': 'bcb76f0ff983d0637f5ddfbc637eceb4590a6790610869fc5dc575e11327bdb7', 'uslugi/lechenie-zubov/zuby-mudrosti/index.html': 'a8a930f478d010ab7ab853460b645e620270c5e7ec56f4a2694e4f1aad8bf6cc', 'uslugi/miofascialnaya-terapia/index.html': '70bcec320b49bc0c2730282bb92b76d52eb650486aef8c42c4a8d68624a92494', 'uslugi/miofascialnyy-massazh/index.html': '54bfaf882d683fdc4ce05acf49115747c763d18607add20c0d39d51a81ab3a61', 'uslugi/miofunkcionalnaya-terapiya/index.html': 'c9ab2ab7315885b98c9554acb18821640dee2db342909223ebfef66f6a8f2699', 'uslugi/neyropsihologia/index.html': '04de60c98984b6de8b6b9f7a490c38fcc3b5935a110097b5e7a7e82b8544936a', 'uslugi/nutriciologiya/index.html': '3f2e6d7dcae8470141038e2c16e0d7e994fa4a119013673352834b09247c7ea2', 'uslugi/orthodontics/index.html': '3c23e42c61543dd2cfe1cbcaf89a32fc1957a2dd713f7022dc72a5becd40ae5e', 'uslugi/ortopedicheskie-stelki/index.html': '84d421df0d7ce15eca42f94b13c70aa4e74f4bfb210cd63eb3e12d9f76d26745', 'uslugi/osteopatiya/index.html': '6bcdf755f73849d8feda37eb8f274a80bc22ffb4e7e0818cfb26117f626d8a63', 'uslugi/preventivnaya-medicina/index.html': 'a4463e665019f945502098b70d92685e58bf02c19dd736c7c4ec01ee7c55d8d7', 'uslugi/prosthetics/index.html': '8afda2eb809f5dcf849efff7d2548adf3a600555c39432ff987dbccc8f217508', 'uslugi/prosthetics/koronki/index.html': 'b346677b601b2cfdd04260b5cd87f606fbc0050ac9e236868bb57efe1817eb6c', 'uslugi/prosthetics/koronki/keramicheskie/index.html': '0ac338dae9b56a60ebd3bf9a54d4ad6485f31abd2e0ae643ed5251b645f18673', 'uslugi/prosthetics/koronki/kompozitnye/index.html': '97fc8b0824f63b7acc8d3b4402b9023f11e53fbc0a54797ac98971e60973ac4a', 'uslugi/prosthetics/mostovidnyy-protez/index.html': 'a8e8959ac39d2157dfdec316d43b66f672d6045417dadf020a21ffd78d992d9b', 'uslugi/prosthetics/viniry/index.html': '7668b35b2b996e77e94370a1711b26d0510ed4809beb3abc7e0417c526ba97d3', 'uslugi/terapia/index.html': 'ff610becb5ed24a6f6260ac5853d9a1082d9102eb510dc1f71b5caa73d9363f8', 'uslygi/index.html': '3e1490374ea49fbd0e1c1c1ecdd069643eaff8ae63e620815b2baf5ace440459'}, 'absent': ['team/tproduct/834140634503-kazantsev-leonid-evgenevich/index.html', 'team/tproduct/338106745633-palunin-vladislav-andreevich/index.html', 'team/tproduct/392688125713-mozharova-ekaterina-sergeevna/index.html'], 'redirects': ['/team/tproduct/834140634503-kazantsev-leonid-evgenevich', '/team/tproduct/834140634503-kazantsev-leonid-evgenevich/', '/team/tproduct/834140634503-kazantsev-leonid-evgenevich/index.html', '/team/tproduct/338106745633-palunin-vladislav-andreevich', '/team/tproduct/338106745633-palunin-vladislav-andreevich/', '/team/tproduct/338106745633-palunin-vladislav-andreevich/index.html', '/team/tproduct/392688125713-mozharova-ekaterina-sergeevna', '/team/tproduct/392688125713-mozharova-ekaterina-sergeevna/', '/team/tproduct/392688125713-mozharova-ekaterina-sergeevna/index.html'], 'public_checks': ['patient/dogovor/AZ_Contract_Sample_Approval_20261015.pdf']}
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
 fd,tmp=tempfile.mkstemp(prefix='.az-lab-document-icons-',dir=p.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  owner=st or p.parent.stat()
  os.chmod(tmp,(st.st_mode & 0o7777) if st else 0o644);os.chown(tmp,owner.st_uid,owner.st_gid)
  os.replace(tmp,p)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def verify_http(name,expected):
 path=MANIFEST['files'].get(name,{}).get('url',name.removesuffix('index.html') if name.endswith('index.html') else name).lstrip('/')
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
def verify_all(prepared,verify,redirect_verify,root):
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
  list(pool.map(lambda item:verify(item[0],item[1][1]),prepared.items()))
  list(pool.map(lambda name:verify(name,root.joinpath(name).read_bytes()),MANIFEST['public_checks']))
  list(pool.map(redirect_verify,MANIFEST['redirects']))
def apply(root,backups,verify=verify_http,redirect_verify=verify_redirect):
 prepared=prepare(root)
 protected={n:(root/n).read_bytes() for n in (*PHOTOS,*MANIFEST['protected'])}
 changes={n:v for n,v in prepared.items() if v[0]!=v[1]}
 if not changes:
  verify_all(prepared,verify,redirect_verify,root);print('ALREADY_INSTALLED=PASS; HTTPS=PASS');return
 backup=backups/('lab-document-icons-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True,mode=0o700)
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
  verify_all(prepared,verify,redirect_verify,root)
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
 print('LAB_DOCUMENT_ICONS=PASS; HTTPS=PASS; PHOTOS_AND_PRICES=UNCHANGED')
 print('ICONS=2; ICON_SIZE=28PX; HTML_UPDATED=1; LAB_GALLERY=UNCHANGED')
 print('BOOKING=UNCHANGED; COMPLAINT_FORM=CLOSED')
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
