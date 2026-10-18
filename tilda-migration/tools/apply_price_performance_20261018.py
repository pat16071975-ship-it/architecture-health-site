#!/usr/bin/env python3
"""Guarded price/readability/performance update; no whole-page replacement."""
import argparse,concurrent.futures,fcntl,gzip,hashlib,json,os,re,shutil,socket,subprocess,sys,tempfile,time
from pathlib import Path
from datetime import datetime,timezone
MANIFEST = {'version': 1, 'purpose': 'Owner-approved Lyubaeva primary appointment 4500 RUB; readable home mobile text; lossless images; scoped CSS/JS gzip', 'approved_at': '2026-10-18', 'files': {'index.html': {'before': '600d2bc8ccc02a893322c5e1a5b39450a45accd2023385fb313d03f0597bfa33', 'after': '7e78dc4a3f6b8a3c829915d4346c3b8ee227b7b4fa2db6386d696b2c5449e68a', 'replacements': [['</head>', '\n<style id="az-home-readable-20261018">\n/* Mobile text flows with its content; desktop and tablet geometry is preserved. */\n@media (max-width:639px){\n #layout-2488705671{display:flex;flex-direction:column;gap:20px;height:auto;padding-top:20px;padding-bottom:24px}\n #visual-2488705671-1470209944682,\n #visual-2488705671-1784635266210000001,\n #visual-2488705671-1784635438553000002,\n #visual-2488705671-1784635533568000003,\n #visual-2488705671-1784635210239000001{\n  position:relative;inset:auto;transform:none;box-sizing:border-box;margin:0 auto;\n  width:calc(100% - 32px);max-width:520px;flex-shrink:0;\n }\n #visual-2488705671-1470209944682,\n #visual-2488705671-1784635266210000001{height:auto}\n #visual-2488705671-1470209944682 > .visual-inner{height:auto;line-height:1.2}\n #visual-2488705671-1784635266210000001,\n #visual-2488705671-1784635266210000001 > .visual-inner{height:auto;font-size:16px;line-height:1.5}\n #visual-2488705671-1784635438553000002,\n #visual-2488705671-1784635533568000003{height:48px}\n #visual-2488705671-1784635210239000001{width:480px;max-width:100%;height:253px}\n #layout-2488705671 .az-home-benefit > .visual-text,\n #layout-2488705671 .az-home-benefit > .visual-text:last-child{font-size:16px}\n #layout-2488705671 .az-home-benefit > .visual-text > .visual-inner{font-size:inherit;line-height:1.45;overflow-wrap:normal;hyphens:auto}\n #layout-2488705671 .az-home-benefit > .visual-text:nth-child(2){min-height:0}\n}\n@media (max-width:479px){#visual-2488705671-1784635210239000001{width:320px;height:186px}}\n</style>\n</head>', 1], ['/assets/ae539bb9ce3fbc525200.png', '/assets/ae539bb9ce3fbc525200-lossless-20261018.webp', 1]]}, 'kompleks-uslugi/arhitektura-zdorovya/index.html': {'before': '8892cddaf963015e2d4bc4cf6d060d74d067d67601fbd1102fb2a839cffa1505', 'after': '76ca2ceaedd80cd343d953ea1f9ffcddab0114b3f254bf7e1aaea67d38b60e13', 'replacements': [['/assets/32ed8370d11f1baeceaa.png', '/assets/32ed8370d11f1baeceaa-lossless-20261018.webp', 1]]}, 'kompleks-uslugi/index.html': {'before': '181c82e0a840f2c669a2418fd175138669a3e6af58e24af58df6218c6777df8e', 'after': 'b3143fffbaedcc51724a7d983f00312ceb5837e92833c4146667178353e470db', 'replacements': [['/assets/66c2653085791a4fa9d8.png', '/assets/66c2653085791a4fa9d8-lossless-20261018.webp', 1], ['/assets/10d1def5a0107a7e496b.png', '/assets/10d1def5a0107a7e496b-lossless-20261018.webp', 1], ['/assets/65e14760056ea4acc6f9.png', '/assets/65e14760056ea4acc6f9-lossless-20261018.webp', 1], ['/assets/32ed8370d11f1baeceaa.png', '/assets/32ed8370d11f1baeceaa-lossless-20261018.webp', 1]]}, 'kompleks-uslugi/korrekciya-vnimaniya-deti/index.html': {'before': '5ff5bfee3df751606feb666575f7b0bb8d32f3f3e9c8f42faff8fe172a1cf6d8', 'after': '80006890a5a98c72b76799c993e0d0248043a096f2f924a5422ec50fc7ce69be', 'replacements': [['/assets/10d1def5a0107a7e496b.png', '/assets/10d1def5a0107a7e496b-lossless-20261018.webp', 1]]}, 'kompleks-uslugi/zdorovaya-spina/index.html': {'before': '7969f4c86ca73922534196980bf932802d5de2fe7dc1eb08f34b97f4ee0fdc3b', 'after': 'b54d2164c51ccae65f21d838ecbcadde07bc8ea7120b08b8d5cebbf84693c6c2', 'replacements': [['/assets/66c2653085791a4fa9d8.png', '/assets/66c2653085791a4fa9d8-lossless-20261018.webp', 1]]}, 'otziv/index.html': {'before': 'dae9dbadf4e47dde45bd710362a36532e08152ba1c6edae44e36b290580e490c', 'after': 'efa1dc8a905f9c55cc7199f764ac197184d5fb7a64e11f34a30d433511ae4753', 'replacements': [['/assets/b8e7a530af57ecf75bc2.png', '/assets/b8e7a530af57ecf75bc2-lossless-20261018.webp', 2]]}, 'team/lubaeva-alena/index.html': {'before': '0f041c7c96efd911bbf7c69254dc374065910449d33fddb4db7041d2c06b7172', 'after': 'a6118277c9707fa9563cc4fbf1b5630a54b33fcb7834034de60d6a273bd46f2f', 'replacements': [['Первичный приём:<br>2 000 ₽, акция — 1 000 ₽', 'Первичный приём:<br>4 500 ₽', 1]]}, 'uslugi/diers_research/index.html': {'before': 'f4c3d0c6f3ced4196b7da2d187def060c3fb3b1e0bb3d4dde273792f0f67b22c', 'after': '0dca5c60ece0fad648ee2451ae115cfb9442cedb7e097daa59f7b6a4772a5af2', 'replacements': [['/assets/2116a70379b63013e71b.png', '/assets/2116a70379b63013e71b-lossless-20261018.webp', 1]]}, 'assets/66c2653085791a4fa9d8-lossless-20261018.webp': {'before': None, 'after': '6aa6704b9f0057db4d6c2bbfee693ab1eb4b879222ebe8f964eb5d2e70ece720', 'bytes': 1508572, 'download': 'https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/125dd873833e8d3359d2cc609cff8c6c4ac09be7/tilda-migration/public/assets/66c2653085791a4fa9d8-lossless-20261018.webp'}, 'assets/10d1def5a0107a7e496b-lossless-20261018.webp': {'before': None, 'after': '926b3acbbc996cd54f5d563e1c0930f9dace598ab97d69e5982adaf2dba001e8', 'bytes': 1347194, 'download': 'https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/125dd873833e8d3359d2cc609cff8c6c4ac09be7/tilda-migration/public/assets/10d1def5a0107a7e496b-lossless-20261018.webp'}, 'assets/65e14760056ea4acc6f9-lossless-20261018.webp': {'before': None, 'after': 'a9f60d7b30ebe555bf8518d3334792c367268051988ce4e642b1b878f4f78a13', 'bytes': 1223862, 'download': 'https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/125dd873833e8d3359d2cc609cff8c6c4ac09be7/tilda-migration/public/assets/65e14760056ea4acc6f9-lossless-20261018.webp'}, 'assets/32ed8370d11f1baeceaa-lossless-20261018.webp': {'before': None, 'after': '9d18476756b3b3bb222fe87da6c5c02a08d7934d173836d05fb2c7d7c8804f56', 'bytes': 1126212, 'download': 'https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/125dd873833e8d3359d2cc609cff8c6c4ac09be7/tilda-migration/public/assets/32ed8370d11f1baeceaa-lossless-20261018.webp'}, 'assets/b8e7a530af57ecf75bc2-lossless-20261018.webp': {'before': None, 'after': 'b23826746a9908ed32dda432b2e58844e41c94432a03339a3c5296e423d9ae1a', 'bytes': 780594, 'download': 'https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/125dd873833e8d3359d2cc609cff8c6c4ac09be7/tilda-migration/public/assets/b8e7a530af57ecf75bc2-lossless-20261018.webp'}, 'assets/2116a70379b63013e71b-lossless-20261018.webp': {'before': None, 'after': 'b31ab720bcd3e2a612db38d3cbe0f13c04692a9a01f8c9dd9d1817ffe72b08ec', 'bytes': 773200, 'download': 'https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/125dd873833e8d3359d2cc609cff8c6c4ac09be7/tilda-migration/public/assets/2116a70379b63013e71b-lossless-20261018.webp'}, 'assets/ae539bb9ce3fbc525200-lossless-20261018.webp': {'before': None, 'after': '26c161bb8826f5934c7f9873e7b37da1be72723d448fa402bbb95f85a6101ab3', 'bytes': 666072, 'download': 'https://raw.githubusercontent.com/pat16071975-ship-it/architecture-health-site/125dd873833e8d3359d2cc609cff8c6c4ac09be7/tilda-migration/public/assets/ae539bb9ce3fbc525200-lossless-20261018.webp'}}, 'protected': {'sitemap.xml': '7d25e8f24835ef0772c360356f8ee9c350921cb602b155d836713e00b05b40a2', 'fonts.css': '3d16ea7238b58a5cf0904098ad236aa6be0b3facccf3a53009af08fcceedce6c', 'robots.txt': '9a93da66b2f7b9bc05ad7429409692dff442b3bf8f988f2937ae21ad9cf6744f', 'site.js': '190dba2936bc2def347a43e94b81e6fe226ee77ae38220db9e4790a17c355361', '404.html': '3fbe5b62b98d38eb325d3b99d47a42fd9d90fdda29ba4ae2002ceab128cab21d', 'assets/az-doctor-cards-20261013.css': '5813a1e65ce1ececfddb2d11dc6669cff3f61e16390b1b01cf870a512b3e62a0', 'assets/az-doctor-cards-wrap-20261013.css': 'b3504521129c394bb669fbc4e420ed6e86c4ed9193b1390a1ee3315dadeeec9a', 'assets/az-mozharova-approved-20261013.jpg': '621542c5ea7b609752813c546548d270d4f1848370eb1901387d48882fa98e4c', 'layouts.css': '66d78398182263a458236c439c1a098fc8390539c7d45511d9240bd54f1a3a71', 'assets/4d67d8e3617de3463719.jpg': '173ab0e4c1ad9b2f86af7545077bdcf1bde17ec73b5d46ad46253cfc3559928f', 'assets/ae539bb9ce3fbc525200.png': '79fb247028b4667e1da2316b9618a512e482bab6ea917507162658cae913f464', 'site.css': '8b2adf1b395ce968f1f116fac27b9a559d7d42265d1686737ec21dc4590406c9', 'assets/2eb303492fc4afc1e2bc.png': 'fad27a52d50d0e7bb74f40ca28841adf1e3525421d111f197afd5599fc03f29c', 'assets/70329c1fef56d8a8eb37.png': 'cbfd46c34d4b110a1e4fcbb69375eb4dcb970d35dd994ad8d6cd57e6a6da91d7', 'patient/dogovor/AZ_Contract_Sample_Approval_20261015.pdf': '9d89e03a3137a305199181faaaf115bc48c9ce43d689142da624e29fcd7fa356', 'assets/30299ba903fd9c7517b3.svg': '56b0d23f22779f58462a2d639983c9872fd25281547a7fcff8b83fecefdf3cd5', 'assets/15e2d44f0157a515db97.svg': 'fbbcd0ecb6b712770fefb1a7c1148662c71eaadbacb118da0805a050a020a15a', 'assets/6ee884df24e48ba85fdd.svg': '834198bf9e56cedf3e8d35fd3346bb6051c2ad33fb981dc2922a9d0a29d600a5', 'assets/b060acc3dfeea870ab98.svg': 'bacb9ce503c878b6f001e2ec319531d90f4c22f5b6c8ac5ab95c64bcf1878848', 'assets/2fa5dbdfb4f891fa7d4a.svg': '1aed8ef96c5e9bc929f7987a5880c0717bd7b3331ddf8badafb6aa8a645c89a3', 'assets/8bd7d0a7b055a94ef022.svg': 'b825c938f7432396bb281ce9b5af3e223f7b2d560f4ec9b780b1adfb6ed85981', 'contacts/index.html': 'fabf3273fe71a644df5d827e1c73932f9bc71bacdd012bf683725c27cb56736e', 'cookie/index.html': 'c760353f5b5d1c80c58640b6099562ce8c079e1c7e8669d48c804993baf530d4', 'o_nas/index.html': 'ad48d44eadaa3fea7dbec387b3f28d04136d0010b766beb66ebedfa98c9684e5', 'patient/dogovor/index.html': '6be73702a53dc21f4f78ccb674438f738376d632c2a790b70e9ef63f5d0eacc0', 'patient/gosgarantii/index.html': '2c93b3eb3ebdaa7956ef422adf9839addde58698cab39b9a3756bd17fc088fc2', 'patient/index.html': 'f5dbb8437f81596c5f179c1e5319eb4302a367c40a4189a5226271618c492ec9', 'patient/licens/index.html': 'a2e66ee0155c3dcb810db71d274ae38e734a68782ebbcbeceaeca68fda385588', 'patient/patient-pravila/index.html': '3c27b8b24796fceae1de2138d12bad8e4c1b894bcfbc252fc2fe60082bee11c6', 'patient/patient-rules/index.html': 'f8aa17dedf8fc6ac4e84fd12d920ce5c4e3e611985194192d2c190d1939d598c', 'patient/priem-patientov/index.html': '2e862feaec378642a679bb54727dd2580b75278d3805b765852b740d9b21f87d', 'patient/supervision/index.html': '7acafd5dba4c4ee4f7893919bf66c47fd8870b34ad457c72e931f9c886819056', 'patient/zhaloba/index.html': '8f9c5de9d865a9f354efced2f3c8c022c360e17ae27386321da274ac7c5dc158', 'politica-site/index.html': '4f045caa1009f89a59d7557b1ce7dd3ef3ba86c724e832579e49160caad8095f', 'politica/index.html': 'f5950dc15209df0993f164687e6cb8d1b871e67c93bef0da3849c4475c1d8144', 'rassrochka/index.html': '398ea1a2ab04512d73d384da502cfd1d27ccffaa97444e329cbaa3ed7d0aadba', 'team/bosak-yana/index.html': '2cac1e5b5bbb2daf038efbd14e907c84c8076b5d3d65c6e73c1bdaa9620a8767', 'team/chirkov-maxim/index.html': '133ac7b96482c72ccfec714ddd5438d5a48ec4b71a69e2117d0fcd38afb7dfcd', 'team/dimitrenko-anna/index.html': '24415803378e5a1d15bcb329edc3da06e7b824ceccda85d6511e1c57c3337806', 'team/golishenkov-vladislav/index.html': '949adfcf28886a24629da60c175bf600ff69c0ed8e9d5f2ea6ca1a76ae6f7592', 'team/holodtseva-valeria/index.html': '3eb74e1eab216d65fdcfa69ca33a780edbf69128ea0f2caf5965c56d8138e6ab', 'team/index.html': 'f33fedfde771b075c7181adddadb00a3adf32e49e7cbcb1ad78b38a6cee15d7f', 'team/lapina-natalia/index.html': '4a043baf83ee7fe497466c90ff53b56779fe832e4e4ce9e15f89bac55c754e2d', 'team/mamontova-lubava/index.html': 'd66c0f39a7b0f1c5740f1c67ca109d17ff1f5a99218dabe0b3507655727a79c5', 'team/movseyan-babken/index.html': '6c3fd0efc5175bc18050d74cc1c87b15b64b440dc3a3b5cdd87bfb44827129ec', 'team/nazirov-uktamjon/index.html': '7ad6d72f55e825b191621f4c1cdd048659943ffc7092c63c971e1c60fd81cc33', 'team/pavlova-anna/index.html': '858ac0f2ed6ce748de522599877cc8594d2eb922dc10f8c060abbc5153e7a95d', 'team/rumyantseva-maria/index.html': '7c509a2bd2a4631996f4b4b1594ce398f6c59d093726352f2a12adcae1d4c54e', 'team/starostenko-vadim/index.html': '29de9bb3c078ad30d1d356f7cfe201a447c422568f3262cf61f7694f0b0ff408', 'team/zubachev-roman/index.html': 'b069dc46613be5dc6d41d843db1adc2153f45118ed0b927fef873a7527c1a681', 'thank/index.html': 'da6fdd5003cc17bc37fce2595a56afebe94eacc72a3b38d31ff5253791cb92be', 'uslugi/craniodontic/index.html': 'acbafffad6be055c1dae333017ee4efa4dbfb9509bd5aa5bd580fb4a41e7ce23', 'uslugi/diers-diagnostica/index.html': '14127566e4bc13c69cb8e8ac0938e4dc976d3acfcbc2b17fd35df0381e609eb7', 'uslugi/doc_educate/index.html': 'a287b63e0136355c429414ab49b05c54b473ad977e60e6e21cea55a949f7c238', 'uslugi/functional_dentistry/index.html': 'c943a7fddd2ce7c1e1f63b5815bb2ef1ddcfcae58a012eceaa397a50e000a77e', 'uslugi/gastroenterologiya/index.html': '33968bca6a1ad6ab71404795b47a9b76be8901151b1e31a8684d7c367ea7f831', 'uslugi/hygiene_prevention/otbelivanie_zoom4/index.html': '46f3deaef969d1093b2549213b3f4187489b5dfc34e06759ff3cdac2b0da01ef', 'uslugi/iggt-terapiya/index.html': '980ba61133a68ac8641249830484509cc8c4b87365d9ee90387e45d94753d28f', 'uslugi/khirurgiya/implantaciya/index.html': 'd13bc6ab9579e61fd40b6f6dc44d65eda268d327eec99d9828f6a8c1089fd460', 'uslugi/konsultatiya-stomatolog/index.html': '5d24f29d87e75cbf8fbf13c0f92b62493ce8c2b1049bbd4e0ab814590618c9ba', 'uslugi/lechenie-zubov/esteticheskaya_restavraciya/index.html': '3676b83d500e4f310a262d6dae545228ef040a3acf239fe319a9dbfd1bd7ad2a', 'uslugi/lechenie-zubov/karies/index.html': '73089ae007ccd3eed7fee214b7c8e7ba541005fb04e03980c515d532191342d1', 'uslugi/lechenie-zubov/kista/index.html': '08a8646f1b7ef333ab1c5e2f3f5d40fdef045fd91d399fe3155650d4e5e0ca74', 'uslugi/lechenie-zubov/periodontit/index.html': 'c5eb0d921b470d57435ce30c03bd72c2ebd78cce0c04de923f3c6187548ff6bb', 'uslugi/lechenie-zubov/pulpit/index.html': '7c5377f2c04d5e1d9484ba5e7139a760425aaf1702314a128110b822a898b438', 'uslugi/lechenie-zubov/udalenie_zubnogo_kamnya/index.html': 'a0d06a6b854efb8f094a8fa1a83aaca03f4e5783b44d6aa143e47cc3cdfe0f67', 'uslugi/lechenie-zubov/zuby-mudrosti/index.html': '9ab83357a86a6cca12cf1117390db5767305d49058c41eee91c98f7132b0bd3c', 'uslugi/miofascialnaya-terapia/index.html': '3ba313421db95e0ddff1fbb3b89f3c77a9421586b553540ed2eafaa8fce7b341', 'uslugi/miofunkcionalnaya-terapiya/index.html': '7345bf0108eb6fc5d6cab42d056ad3717c88f9d9550a01a625ea67b4b87318f3', 'uslugi/neyropsihologia/index.html': 'cf371b7bea38f1764f30cc6e4ac34540db8b71ec4baa252cdab74dda912d029f', 'uslugi/nutriciologiya/index.html': '8a2e54dd5963036ec0ea6ced51b7bf4d0cdaf8cf764096da39d04cb63fae7496', 'uslugi/orthodontics/index.html': '145496b1239b20e45f3054aa713d592f497b92d73851870602831db380dd052f', 'uslugi/ortopedicheskie-stelki/index.html': '7e28a529c76024e395a575ffe93f213a4c0093977a12fdac8f53c826add5fd0c', 'uslugi/osteopatiya/index.html': 'b25b1051ce51300e066033ad2ca17bc655a655dd9c9d48d0097b21910d72c07d', 'uslugi/preventivnaya-medicina/index.html': '97040d39762f205f2041e46fc023eb3fcf129b67c956bdfaf36901253a961242', 'uslugi/prosthetics/index.html': 'e130a00989af70801a6c02bc94b16e3c88c021b17ca2a3fd896c782d9a303b13', 'uslugi/prosthetics/koronki/index.html': '003b20068edca112e1b81e35b1b218c0e9470accae2ae57539b3ceb2bd1f00b4', 'uslugi/prosthetics/koronki/keramicheskie/index.html': 'b50e8cb1f30bc1d94d3eff215e052cc97fd6220c8a71993a0642235ae0d70812', 'uslugi/prosthetics/koronki/kompozitnye/index.html': '2c314555683061474509f543d1301b270627fababf7da5b55f471d644aa61f28', 'uslugi/prosthetics/mostovidnyy-protez/index.html': '5e39d69ced23d5c33c39c26750f3303350c102d2de1a7aff5ed2691fbd485af2', 'uslugi/prosthetics/viniry/index.html': 'dbb48eb05b9c39b9105402e7e675aefc1daea50efde2d62fbe85215a9d6be832', 'uslugi/terapia/index.html': 'f335689d8af73beed2f473eee56341eb3b7c539e5804908ca2e6a42744a4790d', 'uslygi/index.html': '17544ca552e9d39478354064140976fd5a3692bd9e62c96a6cc616cfc700973f', 'uslugi/lechenie-zubov/index.html': '615f8fb23f0e7f0e316b640100b811f0bf9675cf0ad89a087987d2a146d7caf1', 'uslugi/hygiene_prevention/index.html': 'f49f22612fd9d289c0dfdf5d8eca2101994eb373036abfc43b760d89dd91a109', 'uslugi/khirurgiya/index.html': '78679f39cb5db408785c526dbda83fdee16862596fae3c02482b1eadec310e84', 'lab/index.html': 'd6a5f36d3dcd334cf1cf97e6e123a2a513ef2d339e36fba1efae8d78dc705c03', 'uslugi/miofascialnyy-massazh/index.html': '9a7fc4ac21bb1ac33967f20900e6d4c539389f557975c1c8b3a8af6f872ff820', 'assets/66c2653085791a4fa9d8.png': '5a4a6d9213d1f25c32c0306e28aad0702ccdea1e58dc32d3d76323b10c9c50c9', 'assets/10d1def5a0107a7e496b.png': '756f561dca7841ccd2d31f97dc90ad56e98afd5d0950406c49762b2b1f811e9a', 'assets/65e14760056ea4acc6f9.png': '17d3fd58eb917a60421e16ce32012db2b9004efd53de982aae8e85220aa50e28', 'assets/32ed8370d11f1baeceaa.png': 'fbaa72f02f3e6733d398196c2560827c78c309c024ec7badbc144d6bb4c0d157', 'assets/b8e7a530af57ecf75bc2.png': '008c42674dddec10eaabb6e052b736abde7a4cbef68813b8e8ad3990eb939b6e', 'assets/2116a70379b63013e71b.png': '0c4fb243f8fa53d7c82f409d946078c4e9d7ab86c837faef766a03c195b4e133'}, 'absent': ['team/tproduct/834140634503-kazantsev-leonid-evgenevich/index.html', 'team/tproduct/338106745633-palunin-vladislav-andreevich/index.html', 'team/tproduct/392688125713-mozharova-ekaterina-sergeevna/index.html'], 'redirects': ['/team/tproduct/834140634503-kazantsev-leonid-evgenevich', '/team/tproduct/834140634503-kazantsev-leonid-evgenevich/', '/team/tproduct/834140634503-kazantsev-leonid-evgenevich/index.html', '/team/tproduct/338106745633-palunin-vladislav-andreevich', '/team/tproduct/338106745633-palunin-vladislav-andreevich/', '/team/tproduct/338106745633-palunin-vladislav-andreevich/index.html', '/team/tproduct/392688125713-mozharova-ekaterina-sergeevna', '/team/tproduct/392688125713-mozharova-ekaterina-sergeevna/', '/team/tproduct/392688125713-mozharova-ekaterina-sergeevna/index.html'], 'images': [{'original': 'assets/66c2653085791a4fa9d8.png', 'variant': 'assets/66c2653085791a4fa9d8-lossless-20261018.webp', 'before': '5a4a6d9213d1f25c32c0306e28aad0702ccdea1e58dc32d3d76323b10c9c50c9', 'after': '6aa6704b9f0057db4d6c2bbfee693ab1eb4b879222ebe8f964eb5d2e70ece720', 'original_bytes': 2117668, 'variant_bytes': 1508572, 'pixels': 'EXACT_MATCH', 'size': [1536, 1024]}, {'original': 'assets/10d1def5a0107a7e496b.png', 'variant': 'assets/10d1def5a0107a7e496b-lossless-20261018.webp', 'before': '756f561dca7841ccd2d31f97dc90ad56e98afd5d0950406c49762b2b1f811e9a', 'after': '926b3acbbc996cd54f5d563e1c0930f9dace598ab97d69e5982adaf2dba001e8', 'original_bytes': 1928903, 'variant_bytes': 1347194, 'pixels': 'EXACT_MATCH', 'size': [1536, 1024]}, {'original': 'assets/65e14760056ea4acc6f9.png', 'variant': 'assets/65e14760056ea4acc6f9-lossless-20261018.webp', 'before': '17d3fd58eb917a60421e16ce32012db2b9004efd53de982aae8e85220aa50e28', 'after': 'a9f60d7b30ebe555bf8518d3334792c367268051988ce4e642b1b878f4f78a13', 'original_bytes': 1712726, 'variant_bytes': 1223862, 'pixels': 'EXACT_MATCH', 'size': [1536, 1024]}, {'original': 'assets/32ed8370d11f1baeceaa.png', 'variant': 'assets/32ed8370d11f1baeceaa-lossless-20261018.webp', 'before': 'fbaa72f02f3e6733d398196c2560827c78c309c024ec7badbc144d6bb4c0d157', 'after': '9d18476756b3b3bb222fe87da6c5c02a08d7934d173836d05fb2c7d7c8804f56', 'original_bytes': 1621690, 'variant_bytes': 1126212, 'pixels': 'EXACT_MATCH', 'size': [1536, 1024]}, {'original': 'assets/b8e7a530af57ecf75bc2.png', 'variant': 'assets/b8e7a530af57ecf75bc2-lossless-20261018.webp', 'before': '008c42674dddec10eaabb6e052b736abde7a4cbef68813b8e8ad3990eb939b6e', 'after': 'b23826746a9908ed32dda432b2e58844e41c94432a03339a3c5296e423d9ae1a', 'original_bytes': 1531592, 'variant_bytes': 780594, 'pixels': 'EXACT_MATCH', 'size': [1707, 2560]}, {'original': 'assets/2116a70379b63013e71b.png', 'variant': 'assets/2116a70379b63013e71b-lossless-20261018.webp', 'before': '0c4fb243f8fa53d7c82f409d946078c4e9d7ab86c837faef766a03c195b4e133', 'after': 'b31ab720bcd3e2a612db38d3cbe0f13c04692a9a01f8c9dd9d1817ffe72b08ec', 'original_bytes': 1154342, 'variant_bytes': 773200, 'pixels': 'EXACT_MATCH', 'size': [1680, 666]}, {'original': 'assets/ae539bb9ce3fbc525200.png', 'variant': 'assets/ae539bb9ce3fbc525200-lossless-20261018.webp', 'before': '79fb247028b4667e1da2316b9618a512e482bab6ea917507162658cae913f464', 'after': '26c161bb8826f5934c7f9873e7b37da1be72723d448fa402bbb95f85a6101ab3', 'original_bytes': 1024388, 'variant_bytes': 666072, 'pixels': 'EXACT_MATCH', 'size': [1680, 945]}], 'source_rules': {'index.html': {'before': '20618453399998241a216e39e3b1b88e3eae7a833f9cac0bbc8f45afae1a436d', 'after': 'a253b83dd0e3db51df6ea530815fa9fdd15d00ad63ba467e866c73966d530126'}, 'kompleks-uslugi/arhitektura-zdorovya/index.html': {'before': 'c36e6fab51fbf547bce027550db66ec25242529d8472a2d12feffb4bb7224dbc', 'after': '57cadd99d3155cc6f722e5f23563ebac88082eb3ea1eb95cd8826172ba2c388d'}, 'kompleks-uslugi/index.html': {'before': '15814be208e5c929ec870c8021d304950f2773829ea87d9fe6fafd45e3211ff2', 'after': '57ec51ffaad4d7c8a39520b3ee1f6ec624916ae6fa9a065b28a4e40770329e05'}, 'kompleks-uslugi/korrekciya-vnimaniya-deti/index.html': {'before': 'd2eb161395a937f2bed533f7ef652a4b7e5222a20d87260ffec11f1a76266d49', 'after': 'a52eb33f52dcfe4a1ca8f91d35c2a0e5ff33a8506375d978f6a7f1d7e8e32392'}, 'kompleks-uslugi/zdorovaya-spina/index.html': {'before': '1c4ef87dbfe628916718ef26933b92cf131e38c2c7620fe9f64048de83a044bb', 'after': 'a9179c21269f16647ccdc10a3a1a46ee979d6c42c292ce96fd8538bf00273431'}, 'otziv/index.html': {'before': '5ff18251f0e80b75d93159c7cdc30d3a904be58deeed169ae5b5a0326d3add59', 'after': '25f40f3e0fe1f22c7d97a70b9d6f3490f54ad1bda9ff7959d79b88fd6d8db7bb'}, 'team/lubaeva-alena/index.html': {'before': '335cf502ab793ea9204e97b64e08e8adc7dc4a0125844c90589fb20b71216b7d', 'after': '5306e7914a6d29906210060228e661d473d574ded2a81b32c0132b915530d42b'}, 'uslugi/diers_research/index.html': {'before': '3785f6f382d97e532a9dab3dd52ee6c27e51b47d0d346657ee36ac782450b5ab', 'after': 'cb9709d1c9be07eb9f684a85c1eea085c1b0f037448975b8234e6f1e2c8a1655'}}}
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
  elif 'download' in rule:new=fetch_asset(rule)
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
 fd,tmp=tempfile.mkstemp(prefix='.az-price-performance-',dir=p.parent)
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
  if name.endswith('.webp'):
   require(expected[:4]==b'RIFF' and expected[8:12]==b'WEBP','Invalid WebP')
   require('content-type: image/webp' in h.read_text().lower(),'Incorrect WebP Content-Type: '+path)
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
def fetch_asset(rule):
 with tempfile.TemporaryDirectory() as td:
  p=Path(td)/'asset'
  r=subprocess.run(['curl','-fsSL','--retry','2','--connect-timeout','10','--max-time','90',rule['download'],'-o',str(p)],capture_output=True)
  require(r.returncode==0,'Cannot download lossless image')
  b=p.read_bytes();require(len(b)==rule['bytes'] and sha(b)==rule['after'],'Image download checksum mismatch');return b
# Only gzip directives of the unique azmednsk.ru HTTPS server block may change.
def nginx_tokens(text):
    pattern = r'''\s+|\#[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\$\{[^}]+\}|[{};]|(?:\\.|[^\s{};#"'])+'''
    result=[]; pos=0
    for m in re.finditer(pattern,text):
        require(m.start()==pos,'Unsupported nginx syntax');pos=m.end();v=m.group()
        if not v.isspace() and not v.startswith('#'):result.append((v,m.start(),m.end()))
    require(pos==len(text),'Unsupported nginx ending')
    return result

def nginx_nodes(text):
    tokens=nginx_tokens(text);pos=0
    def parse(nested=False):
        nonlocal pos
        nodes=[]
        while pos<len(tokens):
            if tokens[pos][0]=='}':
                require(nested,'Unbalanced nginx brace');end=tokens[pos][1];pos+=1;return nodes,end
            args=[];start=tokens[pos][1]
            while pos<len(tokens) and tokens[pos][0] not in (';','{','}'):
                args.append(tokens[pos][0].strip('\"\''));pos+=1
            require(args and pos<len(tokens),'Invalid nginx directive')
            term=tokens[pos];pos+=1
            if term[0]==';':nodes.append({'args':args,'start':start,'end':term[2],'children':None})
            elif term[0]=='{':
                children,close=parse(True);nodes.append({'args':args,'start':start,'end':close+1,'close':close,'children':children})
            else:raise RuntimeError('Unbalanced nginx directive')
        require(not nested,'Missing nginx closing brace');return nodes,len(text)
    return parse()[0]

def nginx_servers(nodes):
    for node in nodes:
        if node['args']==['server'] and node['children'] is not None:yield node
        elif node['children'] is not None:yield from nginx_servers(node['children'])

def gzip_patch(text):
    candidates=[]
    for node in nginx_servers(nginx_nodes(text)):
        ds=[n for n in node['children'] if n['children'] is None]
        names=[x for n in ds if n['args'][0]=='server_name' for x in n['args'][1:]]
        tls=any(n['args'][0]=='listen' and any(re.search(r'(^|:)443$',v) for v in n['args'][1:]) for n in ds)
        if 'azmednsk.ru' in names and tls:
            require(set(names)<= {'azmednsk.ru','www.azmednsk.ru'},'HTTPS server also serves other domains')
            candidates.append((node,ds))
    if not candidates:return None
    require(len(candidates)==1,'Ambiguous azmednsk HTTPS server')
    node,ds=candidates[0]
    wanted={'gzip':['on'],'gzip_vary':['on'],'gzip_comp_level':['6']}
    existing=[n for n in ds if n['args'][0]=='gzip_types']
    require(len(existing)<=1,'Repeated gzip_types directive')
    types=existing[0]['args'][1:] if existing else []
    for value in ['text/css','application/javascript','text/javascript','application/json','application/xml','text/plain','image/svg+xml']:
        if value not in types:types.append(value)
    wanted['gzip_types']=types
    replacements=[];missing=[]
    for key,values in wanted.items():
        match=[n for n in ds if n['args'][0]==key]
        require(len(match)<=1,'Repeated nginx directive: '+key)
        line=key+' '+' '.join(values)+';'
        if match:
            n=match[0]
            if n['args']!=[key]+values:replacements.append((n['start'],n['end'],line))
        else:missing.append('    '+line)
    if missing:replacements.append((node['close'],node['close'],'\n    # azmednsk static transfer compression 20261018\n'+'\n'.join(missing)+'\n'))
    out=text
    for a,b,value in sorted(replacements,reverse=True):out=out[:a]+value+out[b:]
    return out

def run_nginx(*args):
    r=subprocess.run(['nginx',*args],capture_output=True,timeout=30)
    require(r.returncode==0,'nginx '+ ' '.join(args)+' failed: '+r.stderr.decode(errors='replace')[-1500:])
    return r

def prepare_nginx():
    dump=run_nginx('-T').stdout.decode()
    names=re.findall(r'^# configuration file (/.+):$',dump,re.M)
    require(names,'nginx did not report its configuration files')
    files={}; candidates=[]
    for name in names:
        p=Path(name).resolve()
        if p in files:continue
        old=p.read_bytes();files[p]=old
        after=gzip_patch(old.decode())
        if after is not None:
            require(p.is_relative_to(Path('/etc/nginx')),'Target virtual host must be within /etc/nginx')
            candidates.append((p,old,after.encode(),p.stat()))
    require(len(candidates)==1,'Expected one azmednsk.ru HTTPS configuration; no files changed')
    return candidates[0],files

def verify_gzip(root):
    rows=[]
    for name in ['layouts.css','site.css','site.js']:
        with tempfile.TemporaryDirectory() as td:
            h=Path(td)/'headers';b=Path(td)/'body'
            args=['curl','--noproxy','*','--resolve','azmednsk.ru:443:127.0.0.1','--connect-timeout','5','--max-time','20','-fsS','-w','%{http_code}','-D',str(h),'-o',str(b),'-H','Accept-Encoding: gzip','-H','Cache-Control: no-cache','https://azmednsk.ru/'+name]
            r=subprocess.run(args,capture_output=True)
            for retry in range(5):
                if h.exists() and 'content-encoding: gzip' in h.read_text().lower():break
                time.sleep(0.3);r=subprocess.run(args,capture_output=True)
            headers=h.read_text().lower() if h.exists() else ''
            require(r.returncode==0 and r.stdout==b'200','Compressed HTTPS failed: '+name)
            require('content-encoding: gzip' in headers,'No gzip response: '+name)
            require(any(x.startswith('vary:') and 'accept-encoding' in x for x in headers.splitlines()),'Missing Vary: '+name)
            compressed=b.read_bytes();original=(root/name).read_bytes()
            require(gzip.decompress(compressed)==original,'Compressed bytes differ: '+name)
            if name=='layouts.css':require(len(compressed)<70000,'layouts.css compression ineffective')
            rows.append({'file':name,'original_bytes':len(original),'transfer_bytes':len(compressed)})
    return rows

def apply_with_compression(root,backups,verify=verify_http,redirect_verify=verify_redirect,config_prepare=prepare_nginx,nginx_run=run_nginx,gzip_verify=verify_gzip):
    prepared=prepare(root)  # Downloads and checks all new assets before any live write.
    config,config_files=config_prepare();cp,cold,cnew,cst=config
    changes={n:v for n,v in prepared.items() if v[0]!=v[1]}
    protected={n:(root/n).read_bytes() for n in (*PHOTOS,*MANIFEST['protected'])}
    if not changes and cold==cnew:
        verify_all(prepared,verify,redirect_verify);rows=gzip_verify(root)
        print('ALREADY_INSTALLED=PASS; HTTPS=PASS; GZIP=PASS');return
    backup=backups/('price-performance-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True,mode=0o700)
    for n,(old,new,st) in changes.items():
        if old is not None:
            p=backup/'public'/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/n,p)
            require(p.read_bytes()==old,'Backup mismatch: '+n)
    shutil.copy2(cp,backup/'nginx.before.conf')
    require((backup/'nginx.before.conf').read_bytes()==cold,'nginx backup mismatch')
    (backup/'nginx-plan.json').write_text(json.dumps({'path':str(cp),'before':sha(cold),'after':sha(cnew),'all_config_before':{str(p):sha(b) for p,b in config_files.items()}},indent=2))
    (backup/'manifest.json').write_text(json.dumps(MANIFEST,ensure_ascii=False,indent=2))
    (backup/'absent-before.json').write_text(json.dumps([n for n,v in prepared.items() if v[0] is None]))
    for n,(old,_,_) in prepared.items():require(((root/n).read_bytes() if (root/n).exists() else None)==old,'Concurrent file change: '+n)
    for p,b in config_files.items():require(p.read_bytes()==b,'Concurrent nginx configuration change')
    written=[];config_written=False
    try:
        if cold!=cnew:
            atomic(cp,cnew,cst);config_written=True
            nginx_run('-t')  # Invalid configuration is restored before any reload.
            nginx_run('-s','reload')
        rows=gzip_verify(root)
        for n in sorted(changes,key=lambda name:(not name.endswith('.webp'),name)):
            _,new,st=changes[n];atomic(root/n,new,st);written.append(n)
        for n,(_,new,_) in prepared.items():require((root/n).read_bytes()==new,'Write mismatch: '+n)
        verify_all(prepared,verify,redirect_verify)
        for n,b in protected.items():require((root/n).read_bytes()==b,'Protected content changed: '+n)
        for p,b in config_files.items():require(p.read_bytes()==(cnew if p==cp else b),'Unrelated nginx content changed')
        for n in MANIFEST['absent']:require(not (root/n).exists() and not (root/n).is_symlink(),'Removed profile reappeared')
    except BaseException:
        failures=[]
        for n in reversed(written):
            old,new,st=changes[n]
            try:
                require((root/n).read_bytes()==new,'Concurrent change blocks rollback')
                if old is None:(root/n).unlink()
                else:atomic(root/n,old,st);os.utime(root/n,ns=(st.st_atime_ns,st.st_mtime_ns))
                require(((root/n).read_bytes() if (root/n).exists() else None)==old,'Rollback verification failed')
            except Exception as e:failures.append(n+': '+str(e))
        if config_written:
            try:
                require(cp.read_bytes()==cnew,'Concurrent nginx change blocks rollback')
                atomic(cp,cold,cst);os.utime(cp,ns=(cst.st_atime_ns,cst.st_mtime_ns));nginx_run('-t');nginx_run('-s','reload')
            except Exception as e:failures.append('nginx: '+str(e))
        print('BACKUP='+str(backup));print('ROLLBACK='+('PASS' if not failures else 'FAIL '+str(failures)));raise
    (backup/'gzip-verification.json').write_text(json.dumps(rows,indent=2))
    print('PRICE_AND_PERFORMANCE=PASS; HTTPS=PASS; GZIP=PASS')
    print('LYUBAEVA_PRICE=4500; OTHER_PRICES=UNCHANGED; HOME_MOBILE_TEXT=16PX')
    print('LOSSLESS_IMAGES=7; ORIGINAL_PHOTOS=UNCHANGED; PHOTO_PIXELS=EXACT_MATCH')
    print('COMPLAINT_FORM=CLOSED; DOCUMENTS_CONTACTS_AND_CANONICAL=UNCHANGED')
    for row in rows:print('TRANSFER '+row['file']+'='+str(row['transfer_bytes'])+'; ORIGINAL='+str(row['original_bytes']))
    print('BACKUP='+str(backup))
    print('Следующий шаг: пришлите вывод для внешней проверки сайта.')

def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--check',action='store_true');g.add_argument('--apply',action='store_true');a=p.parse_args()
 require(socket.gethostname()=='az-server','Only az-server');require(os.geteuid()==0,'Use sudo -i');require(ROOT.is_dir(),'Missing site root')
 with open('/run/lock/azmednsk-critical.lock','w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if a.check:prepare(ROOT);prepare_nginx();print('PREFLIGHT=PASS; LIVE_WRITES=NONE');return
  apply_with_compression(ROOT,BACKUPS)
if __name__=='__main__':
 try:main()
 except Exception as e:print('STOP:',e,file=sys.stderr);sys.exit(1)
