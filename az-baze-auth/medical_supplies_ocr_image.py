"""Bounded, local pixel recovery for ruled goods tables.

The first OCR pass is a fallback, not a source of missing values.  Grid cells
are found in the image, including cells for which that pass returned no words.
Every proposed name/number comes from an OCR token in its actual printed cell.
No supplier, document hash, article or expected amount is built into this module.
"""
from __future__ import annotations

import csv
import io
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median

MAX_PIXELS = 16_000_000
MAX_CELLS = 900
MAX_ROWS = 200
MAX_WORDS = 8000
MAX_TSV = 4 * 1024 * 1024


def _groups(indices, gap=4):
    groups = []
    for value in indices:
        value = int(value)
        if not groups or value - groups[-1][-1] > gap:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [int(median(group)) for group in groups]


def _rotation(image, cv2, np):
    """Estimate a small page rotation from long ruled lines, not text guesses."""
    height, width = image.shape
    scale = min(1.0, 1600 / max(width, height))
    sample = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image
    edges = cv2.Canny(sample, 80, 180)
    # Page margins, scanner borders and a photographed screen's toolbar are
    # not useful angle evidence. This does not crop the OCR source image.
    sh, sw = sample.shape
    edges[:int(sh * .10)] = 0
    edges[int(sh * .96):] = 0
    edges[:, :int(sw * .06)] = 0
    edges[:, int(sw * .95):] = 0
    segments = cv2.HoughLinesP(edges, 1, np.pi / 1800, threshold=60,
        minLineLength=max(100, sw * .28), maxLineGap=12)
    angles = []
    if segments is not None:
        for x0, y0, x1, y1 in segments[:2000, 0]:
            dx, dy = int(x1) - int(x0), int(y1) - int(y0)
            angle = math.degrees(math.atan2(dy, dx))
            if abs(angle) <= 7:
                angles.append((angle, math.hypot(dx, dy)))
    angle = 0.0
    if angles:
        best = max(angles, key=lambda a: sum(weight for value, weight in angles
                                            if abs(value-a[0]) <= .4))[0]
        selected = sorted((value, weight) for value, weight in angles if abs(value-best) <= .4)
        half = sum(weight for _, weight in selected) / 2
        running = 0
        for value, weight in selected:
            running += weight
            if running >= half:
                angle = value
                break
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    # Keep the source-sized canvas: output words are mapped back to the
    # original raster, never to an invented or enlarged source page.
    rotated = cv2.warpAffine(image, matrix, (width, height),
                            flags=cv2.INTER_CUBIC, borderValue=255)
    return rotated, cv2.invertAffineTransform(matrix), angle


def _grids(image, cv2, np):
    height, width = image.shape
    binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(35, width // 24), 1)))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, height // 45))))
    spread = max(3, min(11, round(width / 190)))
    connected = cv2.dilate(horizontal | vertical, np.ones((spread, spread), np.uint8))
    hp = cv2.dilate(horizontal, np.ones((spread, 1), np.uint8))
    vp = cv2.dilate(vertical, np.ones((1, spread + 2), np.uint8))
    contours, _ = cv2.findContours(connected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    result = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < width * .35 or h < max(30, height * .025) or w / h > 35:
            continue
        xs = _groups(np.flatnonzero((vp[y:y+h, x:x+w] > 0).sum(axis=0) > h * .32), spread)
        ys = _groups(np.flatnonzero((hp[y:y+h, x:x+w] > 0).sum(axis=1) > w * .52), spread)
        if not (4 <= len(xs) <= 14 and 3 <= len(ys) <= MAX_ROWS + 5):
            continue
        xs, ys = [v+x for v in xs], [v+y for v in ys]
        if min(b-a for a, b in zip(xs, xs[1:])) < 15:
            continue
        row_heights = [b-a for a, b in zip(ys, ys[1:])]
        # Reject a surrounding page frame that happens to contain the table.
        if row_heights[0] > median(row_heights) * 4.5:
            continue
        result.append({"xs": xs, "ys": ys})
    result.sort(key=lambda g: (g["ys"][0], g["xs"][-1]-g["xs"][0]))
    unique = []
    for grid in result:
        if any(abs(grid["ys"][0]-old["ys"][0]) < spread*2
               and abs(grid["ys"][-1]-old["ys"][-1]) < spread*2
               and max(grid["xs"][0], old["xs"][0]) < min(grid["xs"][-1], old["xs"][-1])
               for old in unique):
            continue
        unique.append(grid)
    # Erase detected rules only; thresholds/normalization do not invent text.
    rules = cv2.dilate(horizontal | vertical, np.ones((3, 3), np.uint8))
    cleaned = cv2.bitwise_or(image, rules)
    return unique, cleaned


def _tsv_words(text):
    words = []
    for row in csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not str(row.get("text") or "").strip():
            continue
        try:
            word = [int(row[key]) for key in ("left", "top", "width", "height")]
            confidence = float(row["conf"])
            value = row["text"].strip()
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError("Некорректный результат повторного OCR.") from exc
        if (not 0 <= confidence <= 100 or len(value) > 240 or len(words) >= MAX_WORDS
                or any(v < 0 for v in word[:2]) or min(word[2:]) < 1
                or max(word) > 12000):
            raise ValueError("Превышены границы повторного OCR.")
        words.append([*word, round(confidence, 1), value])
    return words


def _ocr(image, root, command, stem, cv2, psm=6, language="rus+eng", numeric=False):
    path = root / (stem + ".png")
    if not cv2.imwrite(str(path), image):
        raise ValueError("Не удалось подготовить изображение OCR.")
    output = root / (stem + ".tsv")
    argv = ["tesseract", str(path), "stdout", "-l", language, "--psm", str(psm)]
    if numeric:
        argv.extend(["-c","tessedit_char_whitelist=0123456789.,"])
    command([*argv,"tsv"],root,output,4 if numeric else 8,MAX_TSV)
    return _tsv_words(output.read_text(encoding="utf-8"))


def _cell_words(words, box):
    x0, y0, x1, y1 = box
    return [w for w in words if x0 <= w[0]+w[2]/2 < x1 and y0 <= w[1]+w[3]/2 < y1]


def _reading_order(words):
    if not words:
        return []
    height = median(word[3] for word in words)
    rows = []
    for word in sorted(words, key=lambda w: (w[1]+w[3]/2, w[0])):
        center = word[1]+word[3]/2
        if not rows or center - median(w[1]+w[3]/2 for w in rows[-1]) > height*.6:
            rows.append([word])
        else:
            rows[-1].append(word)
    return [word for row in rows for word in sorted(row, key=lambda w: w[0])]


def _text(words):
    return " ".join(word[5] for word in _reading_order(words)).strip()


def _norm(value):
    return " ".join(re.findall(r"[a-zа-я0-9]+", value.casefold().replace("ё", "е")))


def _header(cells):
    roles = {}
    money = []
    for index, words in enumerate(cells):
        text = _norm(re.sub(r"([A-Za-zА-Яа-яЁё])-\s+([A-Za-zА-Яа-яЁё])", r"\1\2", _text(words)))
        compact = text.replace(" ", "")
        if re.search(r"наименован|название|description|product|\bname\b", text):
            roles.setdefault("name", []).append(index)
        elif re.search(r"количеств|кол\s*во|колво|quantity|\bqty\b", text):
            roles.setdefault("quantity", []).append(index)
        elif re.search(r"\bед\b|единиц|\bunit\b", text):
            roles.setdefault("unit", []).append(index)
        elif re.search(r"цена|тариф|price", text):
            roles.setdefault("price", []).append(index)
        elif re.search(r"стоимость|сумма|total|amount", text):
            negative = bool(re.search(r"\b(?:без|исключая|не|excluding|net)\b", text))
            tax = bool(re.search(r"\bсумм(?:а|ы)\s*(?:ндс|налог\w*|акциз\w*)\b|\b(?:ндс|налог\w*|акциз\w*)\s+сумм(?:а|ы)\b|\b(?:vat|tax)\s*amount\b|\bamount\s+(?:of\s+)?(?:vat|tax)\b", text))
            gross = bool(re.search(r"\b(?:с|c|включая)\s*(?:ндс|налогом)\b", text))
            ambiguous = bool(re.search(r"\b(?:ндс|налог\w*|акциз\w*|с|c|включая)\b", text))
            money.append((index, "printed_gross" if gross and not negative and not tax else
                          "printed_single_total" if not negative and not tax and not ambiguous else None))
    if any(len(roles.get(key, [])) != 1 for key in ("name", "quantity", "unit")):
        return None
    result = {key: values[0] for key, values in roles.items() if len(values) == 1}
    gross = [(index, basis) for index, basis in money if basis == "printed_gross"]
    if len(gross) == 1:
        result.update(value=gross[0][0], value_basis=gross[0][1])
    elif len(money) == 1:
        result.update(value=money[0][0], value_basis=money[0][1])
    else:
        result.update(value=None, value_basis=None)
    return result


def _header_consensus(cells):
    """A positive VAT reading cannot override another read's explicit net/tax."""
    proposals=[]
    for key in ("third","second","first"):
        proposal=_header([cell.get(key,[]) for cell in cells])
        if proposal:
            proposals.append(proposal)
    if not proposals:
        return None
    header=dict(proposals[0])
    if any(any(proposal.get(k)!=header.get(k) for k in ("name","quantity","unit"))
           for proposal in proposals[1:]):
        return None
    gross=[p for p in proposals if p.get("value_basis")=="printed_gross"]
    if gross:
        if len({p.get("value") for p in gross})!=1:
            header.update(value=None,value_basis=None)
            return header
        header.update(value=gross[0]["value"],value_basis="printed_gross")
    value_col=header.get("value")
    if value_col is not None:
        texts=[_norm(_text(cells[value_col].get(key,[]))) for key in ("first","second","third")]
        blocked=any(re.search(r"\b(?:без|исключая|не|excluding|net)\b|"
                             r"\bсумм(?:а|ы)\s*(?:ндс|налог\w*|акциз\w*)\b|"
                             r"\b(?:ндс|налог\w*|акциз\w*)\s+сумм(?:а|ы)\b|"
                             r"\b(?:vat|tax)\s*amount\b|\bamount\s+(?:of\s+)?(?:vat|tax)\b",text) for text in texts)
        ambiguous=any(re.search(r"\b(?:ндс|налог\w*|акциз\w*|vat|tax)\b",text) for text in texts)
        any_tax=any(re.search(r"\b(?:ндс|налог\w*|акциз\w*|vat|tax)\b",_norm(_text(cell.get(key,[]))))
                    for cell in cells for key in ("first","second","third"))
        if blocked or header.get("value_basis")=="printed_single_total" and (ambiguous or any_tax):
            header["value_basis"]=None
    return header


def _numeric(words, scale, money=False):
    ordered = _reading_order(words)
    if not ordered or min(w[4] for w in ordered) < 45:
        return None
    text = _text(ordered).replace("\u00a0", " ").strip()
    text = re.sub(r"\s*([,.])\s*", r"\1", text)
    if money:
        pattern = r"(?:\d{1,12}|\d{1,3}(?: \d{3})+)[,.]\d{2}"
    else:
        pattern = r"(?:\d{1,12}|\d{1,3}(?: \d{3})+)(?:[,.]\d{1,"+str(4 if scale==10000 else 3)+r"})?"
    if not re.fullmatch(pattern, text):
        return None
    try:
        value = Decimal(text.replace(" ", "").replace(",", "."))
        if not value.is_finite() or value > Decimal("1000000000") or value*scale != (value*scale).to_integral_value():
            return None
        return format(value, "f")
    except InvalidOperation:
        return None


def _unit(words):
    text = _text(words).strip(" .:;|¦│")
    if not words or min(w[4] for w in words) < 40:
        return ""
    compact = _norm(text).replace(" ", "")
    aliases = {"шт": "Шт", "штук": "Шт", "штука": "Шт", "упак": "УПАК",
               "упаковка": "УПАК", "уп": "УП", "рул": "РУЛ", "рулон": "РУЛ",
               "набор": "НАБОР", "компл": "КОМПЛ", "пара": "ПАРА", "пар": "ПАР",
               "кг": "КГ", "г": "Г", "л": "Л", "мл": "МЛ", "м": "М",
               "pcs": "pcs", "pc": "pc", "pack": "pack", "kg": "kg", "g": "g", "l": "l", "m": "m",
               "ynak": "УПАК", "yпak": "УПАК", "упaк": "УПАК", "pyn": "РУЛ", "pyл": "РУЛ"}
    return aliases.get(compact, "")


def _agree_unit(cell):
    values=[_unit(cell.get(key,[])) for key in ("first","second","third")]
    present=[value for value in values if value]
    if not present:
        return ""
    counts={value:present.count(value) for value in present}
    maximum=max(counts.values())
    winners=[value for value,count in counts.items() if count==maximum]
    return winners[0] if len(winners)==1 else ""


def _agree_cells(cell, scale, money=False):
    values = [_numeric(cell.get(key, []),scale,money) for key in ("first","second","third","fourth")]
    present = [value for value in values if value is not None]
    if not present:
        return None,"unreadable"
    counts={Decimal(value):sum(Decimal(v)==Decimal(value) for v in present) for value in present}
    maximum=max(counts.values())
    winners=[value for value,count in counts.items() if count==maximum]
    if maximum>=2 and len(winners)==1:
        return next(v for v in reversed(present) if Decimal(v)==winners[0]),None
    return None,"conflict" if len(counts)>1 else "single_read"



def _clear_numeric_background(crop, font_height, cv2, np):
    """Remove scanner streaks, keeping glyph-sized ink and nearby decimals.

    This is only a second view of a cell. The unfiltered OCR view remains
    independent evidence; disagreement never authorizes changing a number.
    """
    binary = cv2.threshold(crop,0,255,cv2.THRESH_BINARY_INV|cv2.THRESH_OTSU)[1]
    count,labels,stats,_ = cv2.connectedComponentsWithStats(binary,8)
    cores=[]
    for label in range(1,count):
        x,y,w,h,area=map(int,stats[label])
        if (font_height*.52 <= h <= font_height*1.65
                and w >= font_height*.20 and area >= font_height*1.0):
            cores.append(label)
    if not cores:
        return crop
    keep=set(cores)
    for label in range(1,count):
        if label in keep:
            continue
        x,y,w,h,area=map(int,stats[label])
        if h > font_height*.55 or w > font_height*.45 or area < 2:
            continue
        if any(abs(y+h-(int(stats[c][1])+int(stats[c][3])))<=font_height*.35
               and -font_height*.4 <= x-(int(stats[c][0])+int(stats[c][2])) <= font_height*.65
               for c in cores):
            keep.add(label)
    cleaned=np.full(crop.shape,255,np.uint8)
    for label in keep:
        cleaned[labels==label]=crop[labels==label]
    return cleaned


def _montage(cleaned, grids, cv2, np, narrow_only=False, font_height=None):
    """Reflow physical cells with whitespace; one OCR process for the page."""
    rows = []
    cells = []
    source_h, source_w = cleaned.shape
    zoom = min(2.0, max(1.0, 2500 / source_w))
    padding = 24
    canvas_w = min(3800, max(2200, round(source_w*zoom)))
    x, y, row_h = padding, padding, 0
    for grid_index, grid in enumerate(grids):
        for row_index, (y0, y1) in enumerate(zip(grid["ys"], grid["ys"][1:])):
            for col_index, (x0, x1) in enumerate(zip(grid["xs"], grid["xs"][1:])):
                if narrow_only and row_index > 0 and x1-x0 > (grid["xs"][-1]-grid["xs"][0])*.45:
                    continue
                if len(cells) >= MAX_CELLS:
                    raise ValueError("Слишком много ячеек таблицы.")
                inset = max(2, round(source_w/1300))
                box = (x0+inset, y0+inset, x1-inset, y1-inset)
                if box[2] <= box[0] or box[3] <= box[1]:
                    raise ValueError("Некорректные границы ячейки.")
                crop = cleaned[box[1]:box[3], box[0]:box[2]]
                if narrow_only and row_index>0 and font_height:
                    crop = _clear_numeric_background(crop,font_height,cv2,np)
                factor = min(zoom, (canvas_w-padding*2)/crop.shape[1])
                patch = cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
                h, w = patch.shape
                if x+w+padding > canvas_w:
                    y += row_h+padding*2
                    x, row_h = padding, 0
                if y+h+padding > 10000:
                    raise ValueError("Таблица превышает размер повторного распознавания.")
                cells.append(dict(grid=grid_index, row=row_index, col=col_index,
                                  source=box, target=(x,y,x+w,y+h), scale=factor))
                rows.append((x, y, patch))
                row_h = max(row_h,h)
                x += w+padding*2
                if row_index == 0:
                    y += row_h+padding*2
                    x, row_h = padding, 0
            # A new source row also starts a new montage row. Printed
            # quantities cannot drift into a neighbouring item's name.
            y += row_h+padding*2
            x, row_h = padding, 0
    if y*canvas_w > 32_000_000:
        raise ValueError("Таблица превышает объём повторного распознавания.")
    canvas = np.full((y+padding, canvas_w), 255, np.uint8)
    for x,y,patch in rows:
        canvas[y:y+patch.shape[0], x:x+patch.shape[1]] = patch
    return canvas, cells


def _retry_numbers(cleaned, cells, font_height, root, command, cv2, np):
    """One small batch for uncertain numerical cells, never a value guess."""
    patches=[]
    mapped=[]
    width=1600
    x=y=24
    row_h=0
    for key,cell in cells[:MAX_ROWS*3+4]:
        x0,y0,x1,y1=cell["box"]
        crop=_clear_numeric_background(cleaned[y0:y1,x0:x1],font_height,cv2,np)
        ink=cv2.threshold(crop,0,255,cv2.THRESH_BINARY_INV|cv2.THRESH_OTSU)[1]
        points=cv2.findNonZero(ink)
        if points is None:
            continue
        cx,cy,cw,ch=cv2.boundingRect(points)
        # Tight text with a white border makes a scanned streak outside the
        # actual numeral unable to merge with it into a different token.
        crop=crop[cy:cy+ch,cx:cx+cw]
        factor=min(4.0,max(1.0,36/font_height))
        patch=cv2.resize(crop,None,fx=factor,fy=factor,interpolation=cv2.INTER_CUBIC)
        ph,pw=patch.shape
        if pw>width-48 or ph>500:
            continue
        if y+ph+24>6000:
            break
        patches.append((x,y,patch))
        mapped.append((key,(x,y,x+pw,y+ph),factor,(x0+cx,y0+cy)))
        # A numeric whitelist removes word spaces; separate physical lines
        # prevent several cells from becoming one concatenated OCR number.
        y+=ph+40
        row_h=0
    if not patches:
        return {}
    if y+row_h+24>6000:
        return {}
    canvas=np.full((y+row_h+24,min(width,max(p.shape[1] for _,_,p in patches)+48)),255,np.uint8)
    for x,y,patch in patches:
        canvas[y:y+patch.shape[0],x:x+patch.shape[1]]=patch
    words=_ocr(canvas,root,command,"numeric-retry",cv2,language="eng",numeric=True)
    result={}
    for key,box,factor,offset in mapped:
        raw=_cell_words(words,box)
        result[key]=[[(w[0]-box[0])/factor+offset[0],(w[1]-box[1])/factor+offset[1],
                      w[2]/factor,w[3]/factor,w[4],w[5]] for w in raw]
    return result


def _source_word(word, inverse, shape, offset=(0,0), scale=1.0):
    x, y, w, h, confidence, text = word
    x, y, w, h = x/scale+offset[0], y/scale+offset[1], w/scale, h/scale
    corners = [(x,y),(x+w,y),(x,y+h),(x+w,y+h)]
    mapped = [(inverse[0,0]*px+inverse[0,1]*py+inverse[0,2],
               inverse[1,0]*px+inverse[1,1]*py+inverse[1,2]) for px,py in corners]
    max_h,max_w = shape
    left = max(0, int(math.floor(min(px for px,py in mapped))))
    top = max(0, int(math.floor(min(py for px,py in mapped))))
    right = min(max_w, int(math.ceil(max(px for px,py in mapped))))
    bottom = min(max_h, int(math.ceil(max(py for px,py in mapped))))
    if right <= left or bottom <= top:
        return None
    return [left,top,right-left,bottom-top,confidence,text]


def enhance_page(image_path, directory, command, base_parsed):
    """Return pixel-backed automatic proposals, or an explicit safe fallback.

    `command(argv, directory, stdout_path, seconds, max_size)` is the existing
    sandboxed process runner. Its caller may impose a total wall-clock budget.
    Existing saved reviews are outside this function and are never inspected.
    """
    import cv2
    import numpy as np
    import medical_supplies_ocr as core

    cv2.setNumThreads(1)
    root = Path(directory)
    original = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if original is None or max(original.shape) > 4000 or original.size > MAX_PIXELS:
        raise ValueError("Изображение страницы выходит за допустимые границы.")
    rotated, inverse, angle = _rotation(original, cv2, np)
    grids, cleaned = _grids(rotated, cv2, np)
    if len(grids)>4:
        return dict(base_parsed, recognition_issues=[*base_parsed.get("recognition_issues", []),
            "На странице больше четырёх таблиц; требуется отдельная проверка полноты."])
    if not grids:
        return dict(base_parsed, recognition_issues=[*base_parsed.get("recognition_issues", []),
            "Не удалось выделить ячейки таблицы; сохранён первичный разбор."])
    try:
        full_words = _ocr(cleaned, root, command, "clean-page", cv2)
        montage, cell_map = _montage(cleaned, grids, cv2, np)
        montage_words = _ocr(montage, root, command, "table-cells", cv2, psm=3)
        focused_pixels = cleaned
        focused, focused_map = _montage(focused_pixels, grids, cv2, np, narrow_only=True,
                                         font_height=median(w[3] for w in full_words) if full_words else None)
        focused_words = _ocr(focused, root, command, "table-numbers", cv2, language="rus")
    except core.OCRUnavailable:
        return dict(base_parsed, recognition_retryable=True, recognition_issues=[*base_parsed.get("recognition_issues", []),
            "Повторный разбор ячеек не завершён; сохранён первичный разбор."])
    except ValueError:
        return dict(base_parsed, recognition_issues=[*base_parsed.get("recognition_issues", []),
            "Размер или структура ячеек не поддерживаются; сохранён первичный разбор."])

    cell_data = {}
    for cell in cell_map:
        x,y,_,_ = cell["target"]
        raw = _cell_words(montage_words, cell["target"])
        # Map montage coordinates into the deskewed raster first. This
        # common frame supports reading order and exact source provenance.
        local = [[(w[0]-x)/cell["scale"]+cell["source"][0],
                  (w[1]-y)/cell["scale"]+cell["source"][1],
                  w[2]/cell["scale"], w[3]/cell["scale"], w[4], w[5]] for w in raw]
        cell_data[(cell["grid"],cell["row"],cell["col"])] = {
            "second":local, "first":_cell_words(full_words,cell["source"]), "box":cell["source"]}

    for cell in focused_map:
        x,y,_,_ = cell["target"]
        raw = _cell_words(focused_words, cell["target"])
        local = [[(w[0]-x)/cell["scale"]+cell["source"][0],
                  (w[1]-y)/cell["scale"]+cell["source"][1],
                  w[2]/cell["scale"], w[3]/cell["scale"], w[4], w[5]] for w in raw]
        cell_data[(cell["grid"],cell["row"],cell["col"])]["third"] = local

    retry=[]
    for index,grid in enumerate(grids):
        cols=len(grid["xs"])-1
        header=_header_consensus([cell_data[(index,0,c)] for c in range(cols)])
        if not header:
            continue
        for row_index in range(1,len(grid["ys"])-1):
            for kind in ("quantity","value","price"):
                col=header.get(kind)
                if col is None or kind=="value" and not header.get("value_basis"):
                    continue
                key=(index,row_index,col)
                if _agree_cells(cell_data[key],1000 if kind=="quantity" else 10000 if kind=="price" else 100,kind=="value")[0] is None:
                    retry.append((key,cell_data[key]))
    retryable=False
    extra={}
    if retry:
        try:
            font_height=median(w[3] for w in full_words) if full_words else max(12,original.shape[1]/100)
            extra=_retry_numbers(cleaned,retry,font_height,root,command,cv2,np)
            for key,words in extra.items():
                cell_data[key]["fourth"]=words
        except core.OCRUnavailable:
            # Earlier reads stay valid; unresolved fields remain visibly
            # empty and the caller can retry within a fresh request budget.
            retryable=True

    accepted = []
    issues = (["Повторная проверка части чисел не завершена; её можно повторить."] if retryable else [])
    printed_totals = []
    selected_words = []
    selected_boxes = []
    row_drafts = []
    for index, grid in enumerate(grids):
        cols, row_count = len(grid["xs"])-1, len(grid["ys"])-1
        header = _header_consensus([cell_data[(index,0,c)] for c in range(cols)])
        if header is None:
            continue
        accepted.append(index)
        if header["value_basis"] is None:
            issues.append("Не удалось подтвердить назначение денежной колонки.")
        for r in range(row_count):
            for c in range(cols):
                cell = cell_data[(index,r,c)]
                kind = next((key for key in ("name","quantity","unit","value","price")
                             if header.get(key)==c),None)
                chosen = cell.get("third") or cell["second"] or cell["first"]
                if r > 0 and kind == "name":
                    chosen = cell["second"] or cell["first"]
                elif r > 0 and kind in ("quantity","value","price"):
                    value,_ = _agree_cells(cell,1000 if kind=="quantity" else 10000 if kind=="price" else 100,kind=="value")
                    if value is not None:
                        chosen = next(cell[key] for key in ("fourth","third","second","first")
                                      if _numeric(cell.get(key,[]),1000 if kind=="quantity" else 10000 if kind=="price" else 100,
                                                  kind=="value") is not None
                                      and Decimal(_numeric(cell[key],1000 if kind=="quantity" else 10000 if kind=="price" else 100,
                                                           kind=="value"))==Decimal(value))
                elif r > 0 and kind == "unit":
                    unit_value=_agree_unit(cell)
                    chosen = next((cell[key] for key in ("third","second","first")
                                   if unit_value and _unit(cell.get(key,[]))==unit_value),chosen)
                cell["chosen"] = chosen
                selected_words.extend(chosen)
                selected_boxes.append(cell["box"])
        for r in range(1,row_count):
            name_cell = cell_data[(index,r,header["name"])]
            name_words = name_cell["chosen"]
            name = _text(name_words)
            quantity_cell = cell_data[(index,r,header["quantity"])]
            unit_cell = cell_data[(index,r,header["unit"])]
            value_cell = cell_data.get((index,r,header["value"])) if header["value"] is not None else None
            price_cell = cell_data.get((index,r,header.get("price")))
            price,p_error = _agree_cells(price_cell,10000) if price_cell else (None,None)
            quantity,q_error = _agree_cells(quantity_cell,1000)
            value,v_error = (_agree_cells(value_cell,100,True)
                             if value_cell and header["value_basis"] else (None,"unreadable"))
            unit = _agree_unit(unit_cell)
            if (re.match(r"^(?:итого|всего|total|subtotal|к оплате)\b",_norm(name))
                    and (quantity is None or not unit)):
                # Group subtotals are not document totals, and may occur
                # before further goods. Never stop scanning at such a row.
                final_label=bool(re.fullmatch(r"итого|всего|total|к оплате|сумма к оплате",_norm(name)))
                if final_label and r==row_count-1 and value is not None and header["value_basis"]:
                    printed_totals.append((value,header["value_basis"]))
                continue
            if not name and quantity is None and value is None and not unit:
                issues.append("В таблице есть строка, которую не удалось прочитать.")
                continue
            article = None
            match = re.match(r"^(\d{5,12})\s+(.+)$",name)
            if match:
                article,name = match.groups()
            missing = []
            if quantity is None or Decimal(quantity) <= 0:
                quantity = None
                missing.append("количество")
            if not unit:
                missing.append("единица")
            if value is None:
                missing.append("стоимость")
            if not re.search(r"[А-Яа-яЁёA-Za-z]{2}",name):
                missing.append("название")
            if q_error == "conflict" or v_error == "conflict":
                issues.append("Повторное распознавание числовой ячейки дало разные значения.")
            if len(name)>500:
                raise core.OCRUnavailable("Название позиции превышает допустимую длину.")
            row_drafts.append({"name":name,"article":article,"quantity":quantity,"unit":unit,
                "value":value,"value_basis":header["value_basis"] if value is not None else None,
                "price":price,"action":"unresolved","item_id":None,"folder_id":None,
                "manual":False,"note":("Не распознаны: "+", ".join(missing)+".") if missing else "",
                "_words":[w for c in range(cols) for w in cell_data[(index,r,c)]["chosen"]]})
    if not accepted or not row_drafts:
        return dict(base_parsed, recognition_issues=[*base_parsed.get("recognition_issues", []),
            "Таблица найдена, но её заголовки не распознаны однозначно."])
    if len(accepted)<len(grids):
        issues.append("Не все таблицы страницы удалось разобрать; проверьте оставшиеся таблицы.")
    if len(row_drafts)>MAX_ROWS:
        raise ValueError("Превышен лимит товарных позиций.")
    outside = [word for word in full_words if not any(
        box[0] <= word[0]+word[2]/2 < box[2] and box[1] <= word[1]+word[3]/2 < box[3]
        for box in selected_boxes)]
    evidence = selected_words + outside
    mapped = []
    lookup = {}
    for word in evidence:
        original_word = _source_word(word,inverse,original.shape)
        if original_word is not None:
            mapped.append(original_word)
            lookup[id(word)] = original_word
    if len(mapped)>MAX_WORDS:
        raise ValueError("Превышен лимит слов страницы.")
    lines = core.physical_lines(mapped)
    if len(lines)>core.MAX_LINES:
        raise core.OCRUnavailable("Превышен лимит строк распознавания.")
    line_ids = {id(word):line["id"] for line in lines for word in line["tokens"]}
    used = set()
    for row in row_drafts:
        source_lines = sorted({line_ids[id(lookup[id(word)])] for word in row.pop("_words")
                               if id(word) in lookup})
        source_line = next((line for line in source_lines if line not in used), None)
        if source_line is None:
            raise ValueError("Не удалось связать ячейки с исходным текстом.")
        used.add(source_line)
        row.update(source_line=source_line,source_lines=source_lines)
    unique_totals = set(printed_totals)
    printed_total,total_basis = next(iter(unique_totals)) if len(unique_totals)==1 else (None,None)
    if len(unique_totals)>1:
        issues.append("На странице несколько итогов; общий итог не выбран автоматически.")
    return {"words":mapped,"lines":[{"id":l["id"],"text":l["text"]} for l in lines],
        "candidates":row_drafts,"confidence":round(sum(w[4] for w in mapped)/len(mapped),1) if mapped else None,
        "extractor_version":core.EXTRACTOR_VERSION,"printed_total":printed_total,"total_basis":total_basis,
        "recognition_retryable":retryable,
        "recognition_issues":list(dict.fromkeys(issues)),
        "image_evidence":{"method":"deskewed_grid_cells","rotation_degrees":round(angle,4),
                          "tables":len(accepted),"rows":len(row_drafts),"independent_reads":4 if extra else 3},
        "metadata_lines":[{"id":l["id"],"text":l["text"]} for l in core.physical_lines(full_words)],
        "metadata_first_pass":list(base_parsed.get("lines",[]))}
