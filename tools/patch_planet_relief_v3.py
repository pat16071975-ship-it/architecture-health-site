from pathlib import Path

p = Path('interface-lab/index.html')
s = p.read_text(encoding='utf-8')
marker = '/* planet-surface-v3 — сильный рельеф планет + отдельные плашки названий */'

css = r'''

/* planet-surface-v3 — сильный рельеф планет + отдельные плашки названий */
@media (min-width:721px){
  .direction{
    isolation:isolate!important;
    color:transparent!important;
    background:radial-gradient(circle at 29% 22%,rgba(255,255,255,.96) 0 3%,rgba(246,240,226,.96) 11%,rgba(222,211,191,.98) 29%,rgba(187,185,169,.98) 49%,rgba(142,154,139,.98) 69%,rgba(91,111,96,.99) 86%,rgba(55,72,61,1) 100%)!important;
    border:1px solid rgba(65,88,73,.46)!important;
    box-shadow:inset 15px 12px 22px rgba(255,255,255,.62),inset -20px -17px 30px rgba(43,58,48,.46),inset -5px 8px 15px rgba(181,150,98,.15),0 9px 22px rgba(71,58,38,.10)!important;
    text-shadow:none!important;
  }
  .direction::after,.direction::before{content:"";position:absolute;inset:1px;border-radius:inherit;pointer-events:none}
  .direction::after{z-index:1;opacity:.94;background-blend-mode:multiply,screen,multiply,normal}
  .direction::before{z-index:2;opacity:.98;background-blend-mode:multiply,screen,multiply,normal}
  .direction .planet-label{position:absolute;left:50%;top:50%;z-index:5;transform:translate(-50%,-50%);display:inline-flex;align-items:center;justify-content:center;width:max-content;max-width:88%;min-height:20px;padding:4px 7px;border:1px solid rgba(105,100,82,.34);border-radius:7px;background:rgba(248,244,235,.88);box-shadow:0 2px 8px rgba(54,49,38,.12),inset 0 1px rgba(255,255,255,.78);color:#2f392f!important;font-family:'Montserrat',Inter,Segoe UI,Arial,sans-serif!important;font-size:9.2px!important;line-height:1.08!important;font-weight:600!important;text-align:center!important;text-shadow:none!important;backdrop-filter:blur(.8px)}

  .direction:nth-of-type(1)::after{background:radial-gradient(ellipse at 31% 54%,rgba(57,88,68,.65) 0 20%,rgba(91,106,83,.38) 21% 31%,transparent 33%),radial-gradient(ellipse at 72% 63%,rgba(166,127,76,.52) 0 18%,rgba(207,175,124,.31) 19% 30%,transparent 32%),radial-gradient(ellipse at 59% 18%,rgba(81,106,86,.38) 0 15%,transparent 28%)}
  .direction:nth-of-type(1)::before{background:radial-gradient(circle at 72% 28%,rgba(42,54,45,.90) 0 5%,rgba(112,95,66,.60) 6% 8%,rgba(255,245,222,.82) 9% 11%,rgba(72,92,74,.40) 12% 14%,transparent 16%),radial-gradient(circle at 28% 72%,rgba(48,60,50,.82) 0 6%,rgba(178,144,94,.50) 7% 9%,rgba(255,247,228,.72) 10% 12%,transparent 15%),radial-gradient(circle at 48% 31%,rgba(51,65,53,.75) 0 3%,rgba(245,231,200,.62) 4% 5.5%,transparent 7.5%),radial-gradient(circle at 82% 66%,rgba(47,60,49,.73) 0 3%,rgba(239,224,194,.58) 4% 5.5%,transparent 7.5%)}

  .direction:nth-of-type(2)::after{background:radial-gradient(ellipse at 58% 39%,rgba(167,124,71,.58) 0 24%,rgba(211,177,123,.33) 25% 35%,transparent 37%),radial-gradient(ellipse at 24% 70%,rgba(59,91,71,.63) 0 18%,rgba(94,111,87,.34) 19% 30%,transparent 32%),conic-gradient(from 210deg at 42% 42%,transparent 0 22%,rgba(81,105,84,.26) 23% 40%,transparent 41% 73%,rgba(181,150,98,.21) 74% 89%,transparent 90%)}
  .direction:nth-of-type(2)::before{background:radial-gradient(circle at 24% 32%,rgba(40,52,43,.88) 0 7%,rgba(110,93,65,.55) 8% 10%,rgba(253,243,220,.81) 11% 13%,transparent 16%),radial-gradient(circle at 74% 69%,rgba(46,58,48,.88) 0 5%,rgba(170,137,90,.53) 6% 8.5%,rgba(255,245,223,.72) 9.5% 11.5%,transparent 14.5%),radial-gradient(circle at 78% 23%,rgba(48,62,50,.72) 0 3%,rgba(244,230,200,.59) 4% 5.5%,transparent 7.5%),radial-gradient(circle at 48% 78%,rgba(51,64,52,.68) 0 2.5%,rgba(240,225,194,.54) 3.5% 5%,transparent 7%)}

  .direction:nth-of-type(3)::after{background:radial-gradient(ellipse at 29% 34%,rgba(64,96,75,.66) 0 22%,rgba(103,121,95,.35) 23% 35%,transparent 37%),radial-gradient(ellipse at 69% 67%,rgba(154,112,67,.56) 0 25%,rgba(201,168,117,.30) 26% 37%,transparent 39%),radial-gradient(ellipse at 67% 18%,rgba(72,99,80,.38) 0 16%,transparent 29%)}
  .direction:nth-of-type(3)::before{background:radial-gradient(circle at 67% 28%,rgba(36,49,40,.91) 0 8%,rgba(104,91,64,.61) 9% 11%,rgba(255,245,221,.83) 12% 14%,rgba(69,90,72,.40) 15% 17%,transparent 19%),radial-gradient(circle at 32% 63%,rgba(42,55,45,.83) 0 5%,rgba(169,136,88,.50) 6% 8%,rgba(252,242,220,.69) 9% 11%,transparent 14%),radial-gradient(circle at 19% 28%,rgba(51,64,52,.70) 0 2.5%,rgba(242,229,202,.58) 3.5% 5%,transparent 7%),radial-gradient(circle at 77% 77%,rgba(47,59,49,.66) 0 3%,rgba(237,220,188,.54) 4% 5.5%,transparent 7.5%)}

  .direction:nth-of-type(4)::after{background:radial-gradient(ellipse at 45% 48%,rgba(154,110,64,.61) 0 29%,rgba(208,173,117,.31) 30% 39%,transparent 41%),radial-gradient(ellipse at 77% 26%,rgba(56,89,69,.59) 0 16%,rgba(86,105,82,.32) 17% 28%,transparent 30%),radial-gradient(ellipse at 18% 75%,rgba(71,99,79,.46) 0 18%,transparent 30%)}
  .direction:nth-of-type(4)::before{background:radial-gradient(circle at 24% 27%,rgba(39,51,42,.90) 0 6%,rgba(109,92,63,.56) 7% 9%,rgba(253,244,222,.82) 10% 12%,transparent 15%),radial-gradient(circle at 72% 58%,rgba(36,49,40,.92) 0 8%,rgba(112,92,63,.61) 9% 11%,rgba(255,244,219,.81) 12% 14%,rgba(61,84,67,.39) 15% 17%,transparent 19%),radial-gradient(circle at 51% 24%,rgba(50,63,51,.69) 0 3%,rgba(240,223,191,.55) 4% 5.5%,transparent 7.5%),radial-gradient(circle at 28% 75%,rgba(48,61,50,.71) 0 3.5%,rgba(243,226,194,.56) 4.5% 6%,transparent 8%)}

  .direction:nth-of-type(5)::after{background:conic-gradient(from 35deg at 48% 50%,rgba(149,108,64,.38) 0 10%,transparent 11% 18%,rgba(59,91,72,.52) 19% 34%,transparent 35% 51%,rgba(177,135,81,.40) 52% 69%,transparent 70% 83%,rgba(66,98,78,.43) 84% 100%),radial-gradient(ellipse at 50% 50%,transparent 0 28%,rgba(55,77,62,.22) 29% 44%,transparent 46%)}
  .direction:nth-of-type(5)::before{background:radial-gradient(circle at 79% 27%,rgba(42,54,44,.86) 0 5%,rgba(105,90,63,.53) 6% 8%,rgba(253,244,222,.77) 9% 11%,transparent 14%),radial-gradient(circle at 29% 39%,rgba(40,52,43,.91) 0 7.5%,rgba(116,96,63,.59) 8.5% 11%,rgba(255,243,218,.80) 12% 14%,transparent 17%),radial-gradient(circle at 63% 71%,rgba(46,58,48,.76) 0 3.5%,rgba(242,228,199,.58) 4.5% 6%,transparent 8%),radial-gradient(circle at 21% 76%,rgba(50,62,50,.66) 0 2.5%,rgba(238,220,187,.53) 3.5% 5%,transparent 7%)}

  .direction:nth-of-type(6)::after{background:radial-gradient(ellipse at 31% 65%,rgba(59,92,72,.67) 0 24%,rgba(97,116,90,.34) 25% 36%,transparent 38%),radial-gradient(ellipse at 70% 35%,rgba(168,126,74,.59) 0 20%,rgba(211,178,128,.31) 21% 32%,transparent 34%),radial-gradient(ellipse at 41% 20%,rgba(70,99,79,.36) 0 15%,transparent 27%)}
  .direction:nth-of-type(6)::before{background:radial-gradient(circle at 68% 69%,rgba(36,49,40,.92) 0 8.5%,rgba(106,91,63,.62) 9.5% 12%,rgba(255,244,219,.83) 13% 15%,rgba(66,87,70,.41) 16% 18%,transparent 20%),radial-gradient(circle at 23% 28%,rgba(47,60,49,.80) 0 4%,rgba(244,230,199,.62) 5% 6.5%,transparent 8.5%),radial-gradient(circle at 78% 27%,rgba(50,62,51,.68) 0 3%,rgba(237,220,190,.53) 4% 5.5%,transparent 7.5%),radial-gradient(circle at 42% 43%,rgba(44,56,46,.73) 0 2.5%,rgba(242,228,200,.57) 3.5% 5%,transparent 7%)}

  .direction:nth-of-type(7)::after{background:radial-gradient(ellipse at 55% 57%,rgba(153,109,64,.62) 0 27%,rgba(208,174,121,.31) 28% 39%,transparent 41%),radial-gradient(ellipse at 24% 29%,rgba(57,91,71,.62) 0 19%,rgba(98,115,90,.32) 20% 31%,transparent 33%),radial-gradient(ellipse at 82% 25%,rgba(69,98,79,.37) 0 13%,transparent 25%)}
  .direction:nth-of-type(7)::before{background:radial-gradient(circle at 24% 63%,rgba(38,50,41,.91) 0 8%,rgba(111,92,63,.61) 9% 11%,rgba(254,244,220,.81) 12% 14%,transparent 17%),radial-gradient(circle at 75% 27%,rgba(44,57,46,.84) 0 5%,rgba(169,137,89,.50) 6% 8%,rgba(251,241,220,.69) 9% 11%,transparent 14%),radial-gradient(circle at 69% 76%,rgba(48,60,49,.66) 0 3%,rgba(241,225,194,.54) 4% 5.5%,transparent 7.5%),radial-gradient(circle at 45% 27%,rgba(49,61,50,.67) 0 2.5%,rgba(238,222,191,.52) 3.5% 5%,transparent 7%)}

  .direction:nth-of-type(8)::after{background:conic-gradient(from 180deg at 49% 50%,rgba(59,92,72,.56) 0 18%,transparent 19% 32%,rgba(164,122,71,.50) 33% 49%,transparent 50% 65%,rgba(69,98,79,.43) 66% 79%,transparent 80% 100%),radial-gradient(ellipse at 50% 50%,transparent 0 31%,rgba(76,94,76,.22) 32% 46%,transparent 48%)}
  .direction:nth-of-type(8)::before{background:radial-gradient(circle at 71% 62%,rgba(37,49,40,.91) 0 7%,rgba(109,92,63,.59) 8% 10%,rgba(254,244,220,.81) 11% 13%,transparent 16%),radial-gradient(circle at 26% 27%,rgba(43,55,45,.82) 0 5%,rgba(169,137,89,.49) 6% 8%,rgba(251,241,219,.68) 9% 11%,transparent 13.5%),radial-gradient(circle at 77% 24%,rgba(50,62,51,.66) 0 2.5%,rgba(238,222,192,.52) 3.5% 5%,transparent 7%),radial-gradient(circle at 38% 76%,rgba(48,60,49,.69) 0 3%,rgba(240,224,193,.55) 4% 5.5%,transparent 7.5%)}

  .direction:nth-of-type(9)::after{background:radial-gradient(ellipse at 37% 44%,rgba(56,91,70,.69) 0 28%,rgba(96,116,89,.35) 29% 40%,transparent 42%),radial-gradient(ellipse at 76% 66%,rgba(167,124,72,.53) 0 17%,rgba(211,177,126,.30) 18% 29%,transparent 31%),radial-gradient(ellipse at 77% 22%,rgba(75,103,83,.34) 0 12%,transparent 25%)}
  .direction:nth-of-type(9)::before{background:radial-gradient(circle at 68% 32%,rgba(37,49,40,.92) 0 8%,rgba(111,92,64,.61) 9% 11%,rgba(255,245,221,.82) 12% 14%,rgba(65,87,70,.39) 15% 17%,transparent 19%),radial-gradient(circle at 27% 70%,rgba(43,55,45,.82) 0 5%,rgba(169,137,89,.49) 6% 8%,rgba(252,242,220,.68) 9% 11%,transparent 14%),radial-gradient(circle at 21% 27%,rgba(51,63,52,.68) 0 3%,rgba(239,224,195,.53) 4% 5.5%,transparent 7.5%),radial-gradient(circle at 82% 76%,rgba(48,60,49,.67) 0 2.5%,rgba(238,220,189,.52) 3.5% 5%,transparent 7%)}

  .direction:nth-of-type(10)::after{background:radial-gradient(ellipse at 59% 46%,rgba(161,116,67,.62) 0 29%,rgba(210,175,121,.32) 30% 41%,transparent 43%),radial-gradient(ellipse at 22% 66%,rgba(56,89,69,.62) 0 18%,rgba(94,111,87,.32) 19% 29%,transparent 31%),radial-gradient(ellipse at 30% 20%,rgba(71,99,80,.35) 0 15%,transparent 27%)}
  .direction:nth-of-type(10)::before{background:radial-gradient(circle at 26% 32%,rgba(38,50,41,.91) 0 7%,rgba(110,92,63,.59) 8% 10%,rgba(254,244,220,.81) 11% 13%,transparent 16%),radial-gradient(circle at 74% 68%,rgba(40,52,43,.90) 0 7.5%,rgba(111,93,64,.58) 8.5% 11%,rgba(254,244,220,.79) 12% 14%,transparent 17%),radial-gradient(circle at 73% 23%,rgba(50,62,51,.66) 0 3%,rgba(239,223,193,.53) 4% 5.5%,transparent 7.5%),radial-gradient(circle at 43% 77%,rgba(47,59,48,.68) 0 2.5%,rgba(237,219,189,.52) 3.5% 5%,transparent 7%)}

  .direction:nth-of-type(11)::after{background:conic-gradient(from 300deg at 48% 49%,rgba(58,92,72,.54) 0 17%,transparent 18% 28%,rgba(173,130,77,.51) 29% 46%,transparent 47% 61%,rgba(67,97,78,.45) 62% 80%,transparent 81% 100%),radial-gradient(ellipse at 50% 50%,transparent 0 29%,rgba(75,94,76,.23) 30% 45%,transparent 47%)}
  .direction:nth-of-type(11)::before{background:radial-gradient(circle at 67% 28%,rgba(36,49,40,.92) 0 8%,rgba(107,92,64,.61) 9% 11%,rgba(255,245,221,.82) 12% 14%,rgba(65,87,70,.40) 15% 17%,transparent 19%),radial-gradient(circle at 31% 64%,rgba(43,55,45,.82) 0 5%,rgba(169,137,89,.50) 6% 8%,rgba(252,242,220,.69) 9% 11%,transparent 14%),radial-gradient(circle at 20% 27%,rgba(50,62,51,.67) 0 2.5%,rgba(238,222,191,.52) 3.5% 5%,transparent 7%),radial-gradient(circle at 79% 75%,rgba(48,60,49,.68) 0 3%,rgba(239,222,191,.52) 4% 5.5%,transparent 7.5%)}

  .direction:nth-of-type(12)::after{background:radial-gradient(ellipse at 30% 42%,rgba(162,118,69,.59) 0 21%,rgba(209,175,124,.31) 22% 33%,transparent 35%),radial-gradient(ellipse at 69% 62%,rgba(56,91,70,.66) 0 24%,rgba(96,116,89,.34) 25% 36%,transparent 38%),radial-gradient(ellipse at 72% 18%,rgba(70,100,81,.36) 0 14%,transparent 27%)}
  .direction:nth-of-type(12)::before{background:radial-gradient(circle at 72% 31%,rgba(36,49,40,.92) 0 7.5%,rgba(108,92,64,.60) 8.5% 11%,rgba(255,245,221,.81) 12% 14%,transparent 17%),radial-gradient(circle at 27% 69%,rgba(42,55,45,.84) 0 5.5%,rgba(170,137,88,.50) 6.5% 8.5%,rgba(252,242,220,.70) 9.5% 11.5%,transparent 14.5%),radial-gradient(circle at 23% 27%,rgba(51,63,52,.69) 0 3%,rgba(239,223,193,.53) 4% 5.5%,transparent 7.5%),radial-gradient(circle at 80% 75%,rgba(48,60,49,.67) 0 2.5%,rgba(237,220,190,.52) 3.5% 5%,transparent 7%)}
}

@media (min-width:721px) and (max-width:1180px){
  .direction .planet-label{font-size:7.8px!important;max-width:91%;min-height:18px;padding:3px 5px!important;border-radius:6px!important}
}
'''

if marker not in s:
    needle = '\n\n`;\n\nfunction clearDraft'
    if needle not in s:
        raise SystemExit('responsiveDraft terminator not found')
    s = s.replace(needle, css + needle, 1)

wrap_marker = "doc.querySelectorAll('.direction').forEach(el=>{"
if wrap_marker not in s:
    needle = "  doc.head.appendChild(style);\n  const w=shell.getBoundingClientRect().width;"
    replacement = "  doc.head.appendChild(style);\n  doc.querySelectorAll('.direction').forEach(el=>{\n    if(el.querySelector('.planet-label'))return;\n    const txt=el.textContent.trim();\n    if(!txt)return;\n    el.textContent='';\n    const label=doc.createElement('span');\n    label.className='planet-label';\n    label.textContent=txt;\n    el.appendChild(label);\n  });\n  const w=shell.getBoundingClientRect().width;"
    if needle not in s:
        raise SystemExit('applyDraft insertion point not found')
    s = s.replace(needle, replacement, 1)

s = s.replace("'Черновик: планшет — планеты v2'", "'Черновик: планшет — рельефные планеты v3'")
s = s.replace("'Черновик: ПК — планеты v2'", "'Черновик: ПК — рельефные планеты v3'")
p.write_text(s, encoding='utf-8')
