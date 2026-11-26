"""Local OCR of saved PDF scans; editable evidence-backed drafts, not receipts.

No network/API key, no new schema. Original bytes stay in the inbox.
One bounded page per POST; the browser may request subsequent pages.
"""
import csv
import fcntl
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median

from flask import abort, g, jsonify, make_response, redirect, render_template, request, url_for
import medical_supplies_documents as inbox

KEY = "ocr_review_v1"
EXTRACTOR_VERSION = 4
MAX_PAGES = 20
MAX_WORDS = 8000
MAX_LINES = 1000
MAX_ROWS = 200
MAX_JSON = 16 * 1024 * 1024
MAX_TSV = 4 * 1024 * 1024
ACTIONS = ("unresolved", "existing", "new", "skip")


class OCRUnavailable(ValueError):
    pass


def integer(value, low=1, high=10**12):
    if isinstance(value, bool) or not re.fullmatch(r"\d{1,12}", str(value or "")):
        raise ValueError("Некорректный номер или версия.")
    n = int(value)
    if not low <= n <= high:
        raise ValueError("Номер вне допустимых границ.")
    return n


def clean(value, limit):
    value = str(value or "").strip()
    if len(value) > limit or "\x00" in value:
        raise ValueError("Слишком длинное или некорректное поле.")
    return value


def number(value, scale):
    value = str(value or "").strip().replace("\u00a0"," ").replace("\u202f"," ").replace(",",".")
    if not value:
        return None
    if " " in value:
        if not re.fullmatch(r"\d{1,3}(?: \d{3})+(?:\.\d{1,8})?",value):
            raise ValueError("Проверьте разделители числа.")
        value=value.replace(" ","")
    if not re.fullmatch(r"\d{1,12}(?:\.\d{1,8})?",value):
        raise ValueError("Проверьте числовое поле.")
    try:
        n = Decimal(value)
        if not n.is_finite() or n > Decimal("1000000000") or n * scale != (n * scale).to_integral_value():
            raise ValueError("Число вне допустимой точности.")
        return format(n,"f")
    except InvalidOperation as exc:
        raise ValueError("Проверьте числовое поле.") from exc


def maybe_number(value, scale):
    try:
        return number(value,scale)
    except ValueError:
        return None


def norm(value):
    return " ".join(re.findall(r"[a-zа-я0-9]+",str(value or "").casefold().replace("ё","е")))


def parse_tsv(data):
    """Retain OCR words with bounding boxes and physical row evidence."""
    words=[]
    for row in csv.DictReader(io.StringIO(data),delimiter="\t",quoting=csv.QUOTE_NONE):
        if row.get("level") != "5":
            continue
        token = str(row.get("text") or "").strip()
        if not token:
            continue
        if len(words)>=MAX_WORDS or len(token)>240:
            raise OCRUnavailable("Превышен лимит распознанного текста страницы.")
        try:
            x,y,w,h=(int(row[k]) for k in ("left","top","width","height"))
            conf=float(row["conf"])
        except (TypeError,ValueError,KeyError) as exc:
            raise OCRUnavailable("Некорректный результат распознавания.") from exc
        if not (0<=x<=4000 and 0<=y<=4000 and 0<w<=4000 and 0<h<=4000 and 0<=conf<=100
                and x+w<=4000 and y+h<=4000):
            raise OCRUnavailable("Некорректные координаты распознавания.")
        words.append([x,y,w,h,round(conf,1),token])
    lines = physical_lines(words)
    return {"words":words,
            "lines":[{"id":line["id"],"text":line["text"]} for line in lines],
            "candidates":table_candidates(lines),
            "confidence":round(sum(w[4] for w in words)/len(words),1) if words else None,
            "extractor_version":EXTRACTOR_VERSION}


def physical_lines(words):
    """Use the original grouping so saved OCR line references stay stable."""
    if not words:
        return []
    tolerance=max(4,min(18,median(w[3] for w in words)*0.55))
    groups=[]
    for word in sorted(words,key=lambda w:(w[1]+w[3]/2,w[0])):
        center=word[1]+word[3]/2
        if not groups or abs(center-groups[-1][0])>tolerance:
            groups.append([center,[word]])
        else:
            groups[-1][1].append(word)
    if len(groups)>MAX_LINES:
        raise OCRUnavailable("Превышен лимит строк страницы.")
    lines=[]
    for i,(_,line) in enumerate(groups,1):
        line.sort(key=lambda w:w[0])
        lines.append({"id":i,"text":" ".join(w[5] for w in line),"tokens":line})
    return lines


def derive_page(page_data):
    """Read-only refresh of proposals; preserve raw evidence and human edits."""
    if page_data.get("extractor_version") == EXTRACTOR_VERSION:
        return page_data
    words = page_data.get("words")
    if not isinstance(words, list) or len(words) > MAX_WORDS:
        raise ValueError("Не удалось прочитать сохранённый текст страницы.")
    for word in words:
        if (not isinstance(word, list) or len(word) != 6
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in word[:5])
                or not isinstance(word[5], str) or len(word[5]) > 240
                or not (0 <= word[0] <= 4000 and 0 <= word[1] <= 4000
                        and 0 < word[2] <= 4000 and 0 < word[3] <= 4000
                        and word[0] + word[2] <= 4000 and word[1] + word[3] <= 4000
                        and 0 <= word[4] <= 100)):
            raise ValueError("Повреждён сохранённый текст страницы.")
    lines = physical_lines(words)
    evidence = [{"id":line["id"], "text":line["text"]} for line in lines]
    # Do not reinterpret a saved reference against a different OCR line map.
    if evidence != page_data.get("lines"):
        raise ValueError("Сохранённые строки не совпадают с текстом страницы.")
    return {**page_data, "candidates":table_candidates(lines),
            "extractor_version":EXTRACTOR_VERSION}


def _column_label(token):
    if str(token).strip().startswith("№") or str(token).strip() == "#":
        return "ordinal"
    text = norm(token).replace(" ", "")
    if text.startswith("наимен") or text in ("name", "товары", "товар"):
        return "name"
    if (text.startswith("количеств") or text in ("колво", "quantity", "qty")
            or text in ("кол", "коли", "колич") and str(token).rstrip().endswith("-")):
        return "quantity"
    if text in ("ед", "unit") or text.startswith("едизм") or (
            text.startswith("единиц") and text != "единицу"):
        return "unit"
    if text == "price" or text.startswith(("цена", "тариф")):
        return "price"
    if text == "total" or text.startswith(("сумма", "стоимость")):
        return "value"
    if text in ("артикул", "код", "ставка"):
        return "ignore"
    return None


def _cells(tokens, columns):
    anchors = columns["anchors"]
    cells = {key:[] for key in anchors}
    name_end = columns["name_end"]
    for word in tokens:
        if word[5].strip() in ("|", "||", "¦", "│", "┃"):
            continue
        center = word[0] + word[2] / 2
        ordinal = anchors.get("ordinal")
        if (ordinal is not None and center < anchors["name"][0]
                and re.fullmatch(r"\d+[.)]?", word[5])
                and abs(center-ordinal[0]-ordinal[2]/2) <= max(60, ordinal[2]*2)):
            cells["ordinal"].append(word[5])
            continue
        if center < columns["name_start"]:
            left = [key for key, anchor in anchors.items()
                    if key != "name" and anchor[0] < anchors["name"][0]]
            if left:
                key = min(left, key=lambda k:abs(anchors[k][0]+anchors[k][2]/2-center))
                cells[key].append(word[5])
            continue
        # A broad name column must not be cut at the midpoint of its short
        # heading and the next column: that drops long printed product names.
        if center < name_end:
            key = "name"
        else:
            # Numeric cells are commonly right-aligned. Nearest title centres
            # shift the right half of a broad money column into a narrow code
            # column. Use the next printed heading as its right boundary.
            right = sorted((k for k in anchors if k != "name"
                            and anchors[k][0] >= name_end), key=lambda k:anchors[k][0])
            key = right[0]
            for candidate in right[1:]:
                if center < anchors[candidate][0] - 8:
                    break
                key = candidate
        cells[key].append(word[5])
    return {key:" ".join(values).strip() for key, values in cells.items()}


def _table_header(lines, start):
    found = {}
    band = []
    top = min(w[1] for w in lines[start]["tokens"])
    height = median(w[3] for w in lines[start]["tokens"])
    # These are continuations of printed column titles, not product names.
    # In particular, do not consume the first product's first name line while
    # waiting for its vertically centred quantity on a later physical line.
    qualifiers = set(("вание во чество измерения измерение условное обозначение "
        "национальное код вида товара товаров работы работ услуг услуги описание "
        "выполненных оказанных переданных имущественного права единицу за "
        "без с включая ндс налог налога налогом сумма стоимость объем обьем "
        "акциз акциза налоговая ставка руб коп валюте расчета всего").split())
    for line in lines[start:start+24]:
        if min(w[1] for w in line["tokens"]) - top > max(480, height * 24):
            break
        quantities = found.get("quantity", [])
        # Stop before a real goods row; header qualifiers below the title
        # remain in the band, including a split VAT-inclusive heading.
        if quantities and found.get("name"):
            qty = quantities[0]
            center = qty[0] + qty[2] / 2
            numeric = any(maybe_number(w[5], 1000) is not None
                          and abs(w[0] + w[2] / 2 - center) <= max(qty[2], 60)
                          for w in line["tokens"])
            letters = any(w[0] < qty[0] and re.search(r"[A-Za-zА-Яа-яЁё]{2}", w[5])
                          and _column_label(w[5]) is None for w in line["tokens"])
            ordinal = found.get("ordinal", [])
            numbered = bool(ordinal and any(
                re.fullmatch(r"[1-9]\d*[.)]?", w[5])
                and abs(w[0]+w[2]/2-ordinal[0][0]-ordinal[0][2]/2)
                    <= max(60, ordinal[0][2]*2) for w in line["tokens"]))
            name = found["name"][0]
            right = [w[0] for key, words in found.items() for w in words
                     if key != "name" and w[0] > name[0]]
            name_end = min(right) - 8 if right else qty[0]
            product_text = any(w[0]+w[2]/2 < name_end
                and re.search(r"[A-Za-zА-Яа-яЁё]{2}", w[5])
                and _column_label(w[5]) is None
                and not set(norm(w[5]).split()) <= qualifiers for w in line["tokens"])
            # UPD prints a row of column indexes (1, 1a, 1b, 2, 2a, ...).
            # OCR may read 1a as "la"; that is still the header, not a good.
            words = [w[5] for w in line["tokens"] if w[5] not in ("|", "¦", "│")]
            indexes = len(words) >= 4 and all(re.fullmatch(
                r"(?:\d{1,2}[A-Za-zА-Яа-я]?|[A-Za-zА-Яа-я]{1,2})", token) for token in words)
            if not indexes and ((numeric or numbered) and letters
                                or product_text and not ordinal):
                break
        band.append(line)
        for word in line["tokens"]:
            label = _column_label(word[5])
            if label:
                # A later mention of "товары" in a registration-code column
                # is not a second name column. The title selected at the
                # start of this header establishes that column's position.
                if label == "name" and found.get("name"):
                    continue
                # "Цена (тариф)" is one printed column, not two amounts.
                if label == "price" and found.get(label):
                    previous = found[label][-1]
                    if (abs(word[1]-previous[1]) <= max(word[3], previous[3]) / 2
                            and 0 <= word[0]-previous[0]-previous[2] <= max(25, word[3])):
                        found[label][-1] = [previous[0], min(previous[1], word[1]),
                            word[0]+word[2]-previous[0], max(previous[3], word[3]),
                            min(previous[4], word[4]), previous[5]+" "+word[5]]
                        continue
                found.setdefault(label, []).append(word)
    if len(found.get("name", [])) != 1 or len(found.get("quantity", [])) != 1:
        return None
    name, quantity = found["name"][0], found["quantity"][0]
    if name[0] >= quantity[0] or abs(name[1]-quantity[1]) > max(80, height * 3):
        return None
    anchors = {}
    for label, words in found.items():
        for i, word in enumerate(words):
            anchors[label if len(words) == 1 else f"{label}_{i}"] = word
    right = [w[0] for key, w in anchors.items() if key != "name" and w[0] > name[0]]
    # A centred title is not the left edge of its data column. Only a printed
    # neighbouring column can bound it; otherwise retain the full left area.
    left = [w[0]+w[2] for key, w in anchors.items() if key != "name" and w[0] < name[0]]
    columns = {"anchors":anchors,
               "name_start":max(left, default=0),
               "name_end":min(right)-8,
               "header_end":max(w[1]+w[3] for line in band for w in line["tokens"]),
               "value_key":None, "value_basis":None}
    heading = _cells([w for line in band for w in line["tokens"]], columns)
    money = [key for key in anchors if key == "value" or key.startswith("value_")]
    gross = []
    excluded = set()
    for key in money:
        # Retain the printed qualifier across line wraps: "с на-" / "логом".
        text = norm(re.sub(r"([A-Za-zА-Яа-яЁё])-\s+([A-Za-zА-Яа-яЁё])",
                           r"\1\2", heading[key]))
        net = bool(re.search(r"\bбез\s*(?:ндс|налога)\b", text))
        tax = bool(re.search(r"\bсумма\s*(?:налога|ндс|акциза)\b", text))
        if net or tax:
            excluded.add(key)
        elif re.search(r"\b(?:с|включая)\s*(?:ндс|налогом)\b", text):
            gross.append(key)
    if len(gross) == 1:
        columns.update(value_key=gross[0], value_basis="printed_gross")
    elif len(money) == 1 and money[0] not in excluded:
        # This is the printed amount, not a claim about VAT or a calculation.
        columns.update(value_key=money[0], value_basis="printed_single_total")
    return columns


def _combined_heading(entries):
    """Recognise one printed cell, not separate article/name columns."""
    result = []
    articles = [entry for entry in entries if norm(entry["w"][5]) == "артикул"]
    if len(articles) > 32:
        raise OCRUnavailable("Слишком много заголовков таблиц на странице.")
    for article in articles:
        word = article["w"]
        nearby = sorted((entry for entry in entries
            if word[0] + word[2] <= entry["w"][0] <= word[0] + word[2] + word[3] * 18
            and abs(entry["w"][1] - word[1]) <= max(word[3], entry["w"][3]) * 1.5),
            key=lambda entry: entry["w"][0])
        # The conjunction is evidence that this is a combined printed heading.
        # An independent Артикул column must keep using the general extractor.
        conjunction = next((entry for entry in nearby if norm(entry["w"][5]) == "и"
            and entry["w"][0] - word[0] - word[2] <= word[3] * 2), None)
        if conjunction is None:
            continue
        following = [entry for entry in nearby if entry["w"][0] > conjunction["w"][0]]
        for index, entry in enumerate(following[:4]):
            text = norm(entry["w"][5]).replace(" ", "")
            following_text = (text + norm(following[index+1]["w"][5]).replace(" ", "")
                              if index+1 < len(following) else text)
            if text.startswith("наимен") or following_text.startswith("наимен"):
                result.append((article, conjunction, entry))
                break
    return result


def _page_skew(entries, headings):
    """Bounded baseline projection; coordinates and saved line IDs stay intact."""
    height = median(entry["w"][3] for entry in entries)
    sample = sorted((entry["w"] for entry in entries
        if height * .65 <= entry["w"][3] <= height * 1.4
        and entry["w"][2] >= height and entry["w"][4] >= 65
        and len(entry["w"][5]) >= 2), key=lambda word: (word[1], word[0]))
    if len(sample) > 384:
        sample = [sample[index * (len(sample)-1) // 383] for index in range(384)]
    pairs = []
    for index, first in enumerate(sample):
        for second in sample[index+1:]:
            dx = second[0] + second[2]/2 - first[0] - first[2]/2
            dy = second[1] + second[3] - first[1] - first[3]
            if 250 <= abs(dx) <= 1400 and abs(dy) <= .08 * abs(dx) + 6:
                pairs.append((dx, dy))
    if len(pairs) < 6:
        return 0.0
    if len(pairs) > 6000:
        pairs = [pairs[index * (len(pairs)-1) // 5999] for index in range(6000)]
    sigma = max(3, height * .12)

    def score(slope):
        total = 0.0
        for dx, dy in pairs:
            error = (dy - slope*dx) / sigma
            if abs(error) <= 3:
                total += math.exp(-.5 * error * error)
        return total

    # A repeated dense grid has projection aliases at one-row offsets. The
    # printed combined heading supplies an independent, broad angle prior.
    priors = []
    for heading in headings:
        first = heading[0]["w"]
        end = next((entry["w"] for entry in entries
            if norm(entry["w"][5]) == "товара"
            and heading[2]["w"][0] < entry["w"][0] < max(
                heading[2]["w"][0]+first[3]*20,
                heading[2]["w"][0]+heading[2]["w"][2]+first[3]*6)
            and abs(entry["w"][1]-first[1]) <= first[3]*3), None)
        if end:
            dx = end[0]+end[2]/2-first[0]-first[2]/2
            if dx > first[3]*3:
                prior = (end[1]+end[3]-first[1]-first[3])/dx
                if abs(prior) <= .08:
                    priors.append(prior)
    prior = median(priors) if priors else None
    angles = [index for index in range(-80, 81)
              if prior is None or abs(index*.001-prior) <= .03]
    best = max(angles, key=lambda index: score(index * .001)) * .001
    best = max((best + index*.00025 for index in range(-3, 4)), key=score)
    return best if score(best) >= 6 else 0.0


def _geometry_bands(entries, height, tolerance=.43):
    groups = []
    for entry in sorted(entries, key=lambda entry: (entry["b"], entry["w"][0])):
        if not groups or entry["b"] - median(item["b"] for item in groups[-1]) > height*tolerance:
            groups.append([entry])
        else:
            groups[-1].append(entry)
    return [dict(entries=sorted(group, key=lambda entry: entry["w"][0]),
                 y=median(entry["b"] for entry in group)) for group in groups]


def _printed_numeric(entries, scale, money=False):
    """Read printed digits; do not repair missing cents, OCR letters or totals."""
    if not entries:
        return None
    kept = []
    ordered = sorted(entries, key=lambda entry: entry["w"][0])
    for index, entry in enumerate(ordered):
        word = entry["w"]
        if word[5] in ("|", "¦", "│", "┃"):
            continue
        if not money and word[2] <= 6 and word[3] <= 12 and not re.search(r"\d", word[5]):
            digits = [other["w"] for other in entries if re.search(r"\d", other["w"][5])]
            if digits and (word[0]+word[2] < min(other[0] for other in digits)
                           or word[0] > max(other[0]+other[2] for other in digits)):
                continue
        # A tiny duplicated mark inside an already complete OCR number is not
        # an extra digit. Keep its original evidence; ignore it only here.
        if word[2] <= 6 and word[3] <= 12 and any(
                other is not entry and re.fullmatch(r"\d+[.,]\d{2}", other["w"][5])
                and other["w"][0] <= word[0] <= other["w"][0]+other["w"][2]
                and other["w"][1] <= word[1] <= other["w"][1]+other["w"][3]
                for other in entries):
            continue
        if word[4] < 50:
            return None
        text = word[5]
        if (money and text in ("'", "’", ";") and word[2] <= 6 and word[3] <= 12
                and 0 < index < len(ordered)-1):
            before, after = ordered[index-1]["w"], ordered[index+1]["w"]
            # A small lowered separator physically between the integer and
            # two cents is a printed decimal mark, even if OCR calls it an
            # apostrophe. Never infer a missing mark or repair a low-confidence
            # one, and never change the original token or its bounding box.
            if (re.fullmatch(r"\d+", before[5]) and re.fullmatch(r"\d{2}", after[5])
                    and before[0]+before[2] <= word[0] < word[0]+word[2] <= after[0]
                    and word[1] >= max(before[1]+before[3]*.65, after[1]+after[3]*.65)
                    and word[1]+word[3] <= max(before[1]+before[3], after[1]+after[3])
                        + max(before[3], after[3])*.5):
                text = ","
        kept.append(text)
    text = " ".join(kept).strip()
    text = re.sub(r"\s*([.,])\s*", r"\1", text)
    if money and not re.fullmatch(r"\d{1,12}(?: \d{3})*[.,]\d{2}", text):
        return None
    return maybe_number(text, scale)


def _combined_table_candidates(lines):
    """Recover broad article/name tables from word geometry, not raw rows.

    Raw physical lines remain the immutable OCR evidence. Only internal
    baselines are deskewed. SKU starts and independent right-cell rows delimit
    product blocks, including incomplete names and unreadable numeric cells.
    """
    entries = [dict(w=word, line=line["id"]) for line in lines for word in line["tokens"]]
    if not entries:
        return None
    headings = _combined_heading(entries)
    if not headings:
        return None
    height = median(entry["w"][3] for entry in entries)
    slope = _page_skew(entries, headings)
    for entry in entries:
        word = entry["w"]
        entry["b"] = word[1] + word[3] - slope*(word[0]+word[2]/2)
    result = []
    used_sources = set()
    unit_words = {"шт", "штука", "штук", "упак", "упаковка", "уп", "пак",
                  "рул", "рулон", "компл", "набор", "пара", "пар", "кг", "г", "л", "мл", "м"}
    for heading_index, heading in enumerate(headings):
        article = heading[0]
        header_y = median(entry["b"] for entry in heading)
        next_y = (median(entry["b"] for entry in headings[heading_index+1]) - height
                  if heading_index+1 < len(headings) else float("inf"))
        band = [entry for entry in entries if abs(entry["b"]-header_y) <= height*2
                and entry["w"][0] > heading[2]["w"][0]]
        titles = {}
        title_counts = {}
        for entry in band:
            label = _column_label(entry["w"][5])
            if norm(entry["w"][5]).replace(" ", "") in ("konbo", "колво"):
                label = "quantity"
            if label in ("unit", "quantity", "value", "price"):
                titles.setdefault(label, entry)
                title_counts[label] = title_counts.get(label, 0) + 1
        # This path handles a name/unit/quantity/amount table. More complex
        # price/net/VAT layouts continue through the general parser.
        if ("price" in titles or "quantity" not in titles
                or title_counts.get("value", 0) > 1 or title_counts.get("quantity", 0) > 1):
            return None
        quantity_x = titles["quantity"]["w"][0] + titles["quantity"]["w"][2]/2
        body_top = max(header_y + height*1.35,
            max((entry["b"]+height*.35 for entry in band
                 if norm(entry["w"][5]) in ("ндс", "налогом")), default=header_y))
        footer = [entry for entry in entries if body_top < entry["b"] < next_y
                  and entry["w"][0] < article["w"][0]
                  and re.match(r"^(?:итого|всего|фактическая|подпись|поставщик|покупатель)\b", norm(entry["w"][5]))]
        # A damaged total caption is only a terminator when its isolated
        # amount and the following contractual footer confirm the structure.
        for entry in entries:
            word = entry["w"]
            if not (body_top < entry["b"] < next_y and word[0] < article["w"][0]
                    and word[5].endswith(":") and len(norm(word[5])) <= 12):
                continue
            name_neighbours = [other for other in entries
                if other is not entry and other["w"][0] < quantity_x-height*4
                and abs(other["b"]-entry["b"]) < height*.8
                and re.search(r"[A-Za-zА-Яа-яЁё0-9]", other["w"][5])]
            if (not name_neighbours
                    and any(0 < following["b"]-entry["b"] < height*5 for following in footer)
                    and any(other["w"][0] > quantity_x+height*3
                            and abs(other["b"]-entry["b"]) < height
                            and re.fullmatch(r"\d+[.,]\d{2}", other["w"][5]) for other in entries)
                    and not any(quantity_x-height*2 < other["w"][0] < quantity_x+height*3
                                and abs(other["b"]-entry["b"]) < height
                                and re.search(r"\d", other["w"][5]) for other in entries)):
                footer.append(entry)
        body_end = min((entry["b"]-height*.7 for entry in footer), default=next_y)
        body = [entry for entry in entries if body_top < entry["b"] < body_end
                and entry["w"][5] not in ("|", "||", "¦", "│", "┃")]
        if not body:
            continue
        unit_title = titles.get("unit")
        unit_evidence = [entry for entry in body if norm(entry["w"][5]) in unit_words
                         and quantity_x-height*12 < entry["w"][0] < quantity_x]
        if unit_title:
            unit_x = unit_title["w"][0] + unit_title["w"][2]/2
        elif unit_evidence:
            centres = [entry["w"][0]+entry["w"][2]/2 for entry in unit_evidence]
            unit_x = max(centres, key=lambda centre: (sum(abs(other-centre) <= height*2 for other in centres), centre))
            unit_x = median(centre for centre in centres if abs(centre-unit_x) <= height*2)
        else:
            # A short printed token before the quantity can locate a damaged
            # unit heading without interpreting that token as a valid unit.
            short = [entry for entry in body if quantity_x-height*9 < entry["w"][0] < quantity_x-height*3
                     and entry["w"][2] <= height*3 and re.search(r"[A-Za-zА-Яа-я]", entry["w"][5])]
            if not short:
                return None
            unit_x = median(entry["w"][0]+entry["w"][2]/2 for entry in short)
        value_title = titles.get("value")
        value_evidence = [entry for entry in body if entry["w"][0] > quantity_x+height*2
                          and re.fullmatch(r"\d+[.,]\d{2}", entry["w"][5])]
        if value_title:
            value_x = value_title["w"][0]+value_title["w"][2]/2
        elif value_evidence:
            value_x = median(entry["w"][0]+entry["w"][2]/2 for entry in value_evidence)
        else:
            return None
        if not unit_x < quantity_x < value_x:
            return None
        unit_evidence = [entry for entry in unit_evidence
                         if abs(entry["w"][0]+entry["w"][2]/2-unit_x) <= height*2]
        name_end = min((entry["w"][0] for entry in unit_evidence), default=unit_x-height*1.5) - height*.2
        unit_end = (unit_x+quantity_x)/2
        quantity_end = (quantity_x+value_x)/2
        for entry in body:
            x = entry["w"][0]+entry["w"][2]/2
            entry["cell"] = ("name" if x < name_end else "unit" if x < unit_end
                             else "quantity" if x < quantity_end else "value")
        name_entries = [entry for entry in body if entry["cell"] == "name"]
        if not name_entries:
            continue
        # Tall OCR boxes often extend upward while retaining the correct
        # printed baseline. Never replace that baseline with their top edge.
        names = _geometry_bands(name_entries, height, .75)
        names = [row for row in names if any(re.search(r"[A-Za-zА-Яа-яЁё0-9]", entry["w"][5])
                                            for entry in row["entries"])]
        if not names:
            continue
        right = _geometry_bands([entry for entry in body if entry["cell"] != "name"], height, .8)
        # Isolated low OCR specks do not define a product row. Unreadable cells
        # in two separate columns still do, so the corresponding name is kept.
        right = [row for row in right if len({entry["cell"] for entry in row["entries"]}) >= 2
                 or any(norm(entry["w"][5]) in unit_words
                        or re.search(r"\d", entry["w"][5]) for entry in row["entries"])]
        starts = [0]
        sku_at = {}
        for index, row in enumerate(names):
            first = row["entries"][0]
            if (re.fullmatch(r"\d{5,12}", first["w"][5])
                    and first["w"][0] < article["w"][0]
                    and any(re.search(r"[A-Za-zА-Яа-яЁё]{2}", entry["w"][5]) for entry in row["entries"][1:])):
                if index not in starts:
                    starts.append(index)
                sku_at[index] = first
        initial = sorted(starts)
        for slot, start in enumerate(initial):
            stop = initial[slot+1] if slot+1 < len(initial) else len(names)
            low = names[start]["y"]-height*.65
            high = names[stop]["y"]-height*.65 if stop < len(names) else body_end
            numeric_rows = [row for row in right if low <= row["y"] < high]
            for previous, current in zip(numeric_rows, numeric_rows[1:]):
                boundary = (previous["y"]+current["y"])/2
                split = next((index for index in range(start+1, stop)
                              if names[index]["y"] > boundary), None)
                if split is not None and names[split]["y"] <= current["y"]+height*.6:
                    starts.append(split)
        starts = sorted(set(starts))
        heading_text = " ".join(entry["w"][5] for entry in band
                                if entry["w"][0] > quantity_end)
        gross = bool(re.search(r"\b(?:с|c|включая)\s+(?:ндс|налогом)\b", norm(heading_text)))
        for slot, start in enumerate(starts):
            stop = starts[slot+1] if slot+1 < len(starts) else len(names)
            text_entries = [entry for row in names[start:stop] for entry in row["entries"]]
            low = names[start]["y"]-height*.65
            high = names[stop]["y"]-height*.65 if stop < len(names) else body_end
            numeric_entries = [entry for row in right if low <= row["y"] < high for entry in row["entries"]]
            sku = sku_at.get(start)
            name = " ".join(entry["w"][5] for entry in text_entries if entry is not sku).strip()
            if not re.search(r"[A-Za-zА-Яа-яЁё]{2}", name):
                continue
            if len(name) > 500:
                raise OCRUnavailable("Название позиции превышает допустимую длину. Проверьте исходный текст.")
            cells = {key:[entry for entry in numeric_entries if entry["cell"] == key]
                     for key in ("unit", "quantity", "value")}
            quantity = _printed_numeric(cells["quantity"], 1000)
            quantity = quantity if quantity is not None and Decimal(quantity) > 0 else None
            value = _printed_numeric(cells["value"], 100, money=True)
            raw_unit = " ".join(entry["w"][5] for entry in sorted(cells["unit"], key=lambda entry:entry["w"][0]))
            compact_unit = raw_unit.replace(" ", "")
            unit = (raw_unit if norm(raw_unit) in unit_words else compact_unit
                    if norm(compact_unit) in unit_words else "")
            if not all(entry["w"][4] >= 50 for entry in cells["unit"]):
                unit = ""
            source_lines = sorted({entry["line"] for entry in text_entries+numeric_entries})
            preferred = (sku or text_entries[0])["line"]
            source = next((line for line in [preferred]+source_lines if line not in used_sources), None)
            if source is None:
                raise OCRUnavailable("Не удалось однозначно связать позиции со строками исходного текста.")
            used_sources.add(source)
            missing = [label for label, present in (("количество", quantity), ("единицу", unit), ("стоимость", value))
                       if present is None or present == ""]
            note = ("Проверьте по исходнику: " + ", ".join(missing) + ".") if missing else ""
            result.append({"source_line":source, "source_lines":source_lines,
                "name":name, "unit":unit, "quantity":quantity, "price":None,
                "value":value, "value_basis":("printed_gross" if gross else "printed_single_total") if value is not None else None,
                "action":"unresolved", "item_id":None, "folder_id":None, "note":note, "manual":False})
    if len(result) > MAX_ROWS:
        raise OCRUnavailable("Превышен лимит товарных позиций страницы.")
    return result


def table_candidates(lines):
    """Printed goods proposals with evidence; never infer money or quantities."""
    recovered = _combined_table_candidates(lines)
    if recovered is not None:
        return recovered
    result, segment = [], []
    columns = None

    def finish():
        groups, pending = [], []
        numeric_keys = [key for key in columns["anchors"]
                        if key in ("quantity", "price", "value") or key.startswith("value_")]

        def close(a, b):
            return abs(a["y"]-b["y"]) <= max(a["height"], b["height"])*2.5

        def amounts(part):
            return any(part["cells"].get(key) for key in numeric_keys)

        for part in segment:
            cells = part["cells"]
            named = bool(re.search(r"[A-Za-zА-Яа-яЁё]{2}", cells.get("name", "")))
            numbered = bool(re.fullmatch(r"[1-9]\d*[.)]?", cells.get("ordinal", "")))
            # A printed numbered good remains a good when one numerical field
            # is unreadable. An unnumbered row needs additional cell evidence.
            row = part["quantity"] is not None or (named and cells.get("unit") and amounts(part))
            previous = groups[-1] if groups else None
            has_amounts = bool(previous and any(amounts(piece) for piece in previous))
            if numbered or row and (not previous or has_amounts or not close(previous[-1], part)):
                pieces = [part]
                if not numbered and pending and close(pending[-1], part):
                    pieces = pending + pieces
                groups.append(pieces)
                pending = []
            elif previous and close(previous[-1], part) and (
                    part["name_fragment"] or row or not has_amounts and amounts(part)):
                # Direction follows the printed row boundary. In particular a
                # third name line never jumps to a nearer quantity below it.
                previous.append(part)
            elif part["name_fragment"]:
                if pending and not close(pending[-1], part):
                    pending = []
                pending.append(part)
            else:
                pending = []

        for pieces in groups:
            cells = {key:" ".join(piece["cells"].get(key, "") for piece in pieces
                                   if piece["cells"].get(key)).strip()
                     for key in columns["anchors"]}
            name = cells.get("name", "")
            if not re.search(r"[A-Za-zА-Яа-яЁё]{2}", name) or len(name) > 500:
                continue
            source = next((part for part in pieces if part["quantity"] is not None), pieces[0])
            quantity = maybe_number(cells.get("quantity", ""), 1000)
            quantity = quantity if quantity is not None and Decimal(quantity) > 0 else None
            unit = cells.get("unit", "").strip("|¦│┃ ")
            if len(unit) > 30 or re.search(r"\d", unit):
                unit = ""
            value = maybe_number(cells.get(columns["value_key"], ""), 100)
            result.append({"source_line":source["line"]["id"],
                           "source_lines":[item["line"]["id"] for item in pieces],
                           "name":name, "unit":unit, "quantity":quantity,
                           "price":maybe_number(cells.get("price", ""), 10000),
                           "value":value, "value_basis":columns["value_basis"] if value is not None else None,
                           "action":"unresolved", "item_id":None, "folder_id":None,
                           "note":"", "manual":False})
        segment.clear()

    for index, line in enumerate(lines):
        tokens = line["tokens"]
        if any(_column_label(word[5]) == "name" for word in tokens):
            header = _table_header(lines, index)
            if header:
                if columns:
                    finish()
                columns = header
                continue
        if not columns or min(w[1] for w in tokens) <= columns["header_end"]:
            continue
        if re.match(r"^(итого|всего|total|к оплате|сумма к оплате|руководитель|главный бухгалтер|"
                    r"отпустил|получил|отпуск разрешил|ответственный|подпись|примечание|основание)\b", norm(line["text"])):
            finish()
            columns = None
            continue
        cells = _cells(tokens, columns)
        quantity = maybe_number(cells.get("quantity", ""), 1000)
        quantity = quantity if quantity is not None and Decimal(quantity) > 0 else None
        name_fragment = bool(cells.get("name") and all(not value for key, value in cells.items()
                            if key != "name" and not key.startswith("ignore")))
        segment.append({"line":line, "cells":cells, "quantity":quantity, "name_fragment":name_fragment,
                        "y":median(w[1]+w[3]/2 for w in tokens), "height":median(w[3] for w in tokens)})
    if columns:
        finish()
    return result[:MAX_ROWS]


def _command(argv, directory, output, seconds, max_size):
    # Fixed commands only. Child resource limits and wall-clock timeout,
    # private cwd/HOME, no shell or document-derived executable arguments.
    env={"PATH":"/usr/bin:/bin","LC_ALL":"C.UTF-8","HOME":str(directory),
         "TMPDIR":str(directory),"OMP_THREAD_LIMIT":"1"}
    limit=shutil.which("prlimit",path=env["PATH"])
    binary=shutil.which(argv[0],path=env["PATH"])
    if not limit or not binary:
        raise OCRUnavailable("На сервере не установлены инструменты OCR.")
    args=[limit,"--as=805306368","--fsize="+str(max_size),"--nofile=128",
          "--cpu="+str(seconds+2),"--",binary,*argv[1:]]
    try:
        with output.open("wb") as dest:
            subprocess.run(args,cwd=directory,env=env,stdin=subprocess.DEVNULL,
                           stdout=dest,stderr=subprocess.DEVNULL,check=True,
                           timeout=seconds,close_fds=True)
    except (OSError,subprocess.SubprocessError) as exc:
        raise OCRUnavailable("Не удалось распознать страницу в пределах лимита. Повторите или проверьте оригинал.") from exc
    if output.stat().st_size>max_size:
        raise OCRUnavailable("Превышен лимит распознавания.")


def recognize_pdf_page(raw,page,lock_directory):
    """One page, no database writes or long SQLite transaction."""
    if not raw.startswith(b"%PDF-") or not 0<len(raw)<=inbox.MAX_BYTES:
        raise OCRUnavailable("Поддерживается PDF-оригинал до 20 МБ.")
    lock=Path(lock_directory)/".med-supply-ocr.lock"
    try:
        fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    except OSError as exc:
        raise OCRUnavailable("Служебное хранилище OCR недоступно.") from exc
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
            raise OCRUnavailable("Небезопасный служебный файл OCR.")
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OCRUnavailable("Сейчас распознаётся другой документ. Повторите чуть позже.") from exc
        with tempfile.TemporaryDirectory(prefix="az-med-ocr-") as dirname:
            root=Path(dirname)
            pdf=root/"source.pdf"
            pdf.write_bytes(raw)
            report=root/"info.txt"
            _command(["pdfinfo",str(pdf)],root,report,3,65536)
            match=re.search(r"^Pages:\s+(\d+)\s*$",report.read_text(encoding="utf-8",errors="replace"),re.M)
            if not match or not 1<=int(match.group(1))<=MAX_PAGES:
                raise OCRUnavailable("PDF должен содержать от 1 до 20 страниц.")
            count=int(match.group(1))
            if page>count:
                raise ValueError("Такой страницы нет в PDF.")
            prefix=root/"page"
            # Poppler limits raster dimensions. Its image file is covered by
            # the same per-process fsize limit (64 MiB).
            _command(["pdftoppm","-f",str(page),"-l",str(page),"-scale-to","3508",
                      "-png","-singlefile",str(pdf),str(prefix)],root,root/"render.log",6,64*1024*1024)
            image=prefix.with_suffix(".png")
            if not image.is_file():
                raise OCRUnavailable("Не удалось получить изображение страницы.")
            tsv=root/"page.tsv"
            _command(["tesseract",str(image),"stdout","-l","rus+eng","--psm","3","tsv"],
                     root,tsv,17,MAX_TSV)
            parsed=parse_tsv(tsv.read_text(encoding="utf-8"))
            return count,parsed
    finally:
        os.close(fd)


def document(conn,doc_id,raw=False):
    if not inbox.ready(conn):
        raise OCRUnavailable("Хранилище документов не подготовлено.")
    columns="id,sha256,size_bytes,original_name,content_type,version,status,fields_json"
    if raw:
        columns+=",original_bytes"
    row=conn.execute("SELECT "+columns+" FROM med_supply_documents WHERE id=?",(doc_id,)).fetchone()
    if row is None:
        raise LookupError("Документ не найден.")
    if row["content_type"]!="application/pdf":
        raise ValueError("Разбор сканов пока поддерживает PDF.")
    if raw and (len(row["original_bytes"])!=row["size_bytes"] or hashlib.sha256(row["original_bytes"]).hexdigest()!=row["sha256"]):
        raise ValueError("Контрольная сумма оригинала не совпадает.")
    fields=json.loads(row["fields_json"])
    if not isinstance(fields,dict):
        raise ValueError("Повреждены поля документа.")
    state=fields.get(KEY,{"page_count":None,"pages":{}})
    if not isinstance(state,dict) or not isinstance(state.get("pages"),dict):
        raise ValueError("Повреждён черновик разбора.")
    return row,fields,state


def store(conn,doc_id,version,sha,mutate,actor,action,details):
    conn.execute("BEGIN IMMEDIATE")
    try:
        row,fields,state=document(conn,doc_id)
        if row["version"]!=version or row["sha256"]!=sha:
            raise inbox.Conflict("Карточка изменилась. Обновите страницу.")
        mutate(state)
        fields[KEY]=state
        payload=json.dumps(fields,ensure_ascii=False,separators=(",",":"),allow_nan=False)
        if len(payload.encode("utf-8"))>MAX_JSON:
            raise ValueError("Превышен объём черновика.")
        # The existing status is informational; a checked OCR draft is not
        # evidence of a posted receipt. Every page must be explicitly checked.
        count=state.get("page_count")
        reviewed=bool(count and all(state["pages"].get(str(i),{}).get("checked") for i in range(1,count+1)))
        now=inbox._now()
        conn.execute("""UPDATE med_supply_documents SET fields_json=?,status=?,
            version=version+1,updated_by=?,updated_at=? WHERE id=?""",
            (payload,"reviewed" if reviewed else "review",actor,now,doc_id))
        inbox._event(conn,doc_id,action,actor,{"version":version},
                     {"version":version+1,**details,"stock_posted":False},now)
        conn.commit()
        return version+1
    except BaseException:
        conn.rollback()
        raise


def catalog(conn):
    return [dict(row) for row in conn.execute("""SELECT i.id,i.name,i.unit,i.excluded,
        d.folder_id,f.name AS folder FROM med_supply_items i
        LEFT JOIN med_supply_daily_items d ON d.item_id=i.id
        LEFT JOIN med_supply_folders f ON f.id=d.folder_id ORDER BY i.name,i.id""")]


def suggestions(name,unit,items):
    needle=norm(name)
    words=set(needle.split())
    if not words:
        return []
    ranked=[]
    for item in items:
        other=norm(item["name"])
        ow=set(other.split())
        score=len(words&ow)/max(len(words|ow),1)
        if needle==other:
            score=2
        elif score<0.4:
            continue
        ranked.append((score,norm(unit)==norm(item["unit"]),item["id"],item))
    ranked.sort(key=lambda x:(-x[0],-int(x[1]),x[2]))
    return [r[3] for r in ranked[:3]]


def parse_review(form,page_data,items,folders):
    rows=[]
    keys=("source_line","name","unit","quantity","price","value","action","item_id","folder_id","note")
    lists={k:form.getlist(k) for k in keys}
    sizes={len(v) for v in lists.values()}
    if len(sizes)!=1 or next(iter(sizes))>MAX_ROWS:
        raise ValueError("Некорректное количество строк.")
    known_lines={l["id"] for l in page_data["lines"]}
    known_items={i["id"]:i for i in items}
    folder_ids={f["id"] for f in folders}
    used=set()
    for vals in zip(*(lists[k] for k in keys)):
        r=dict(zip(keys,vals))
        name=clean(r["name"],500)
        if not name and not any(r[k].strip() for k in ("quantity","price","value","item_id","folder_id","note")):
            continue
        line=integer(r["source_line"],1,MAX_LINES) if r["source_line"] else None
        if line is not None and (line not in known_lines or line in used):
            raise ValueError("Повторная или неизвестная строка OCR.")
        if line is not None:
            used.add(line)
        action=r["action"]
        if action not in ACTIONS:
            raise ValueError("Неизвестное решение по строке.")
        item_id=integer(r["item_id"]) if r["item_id"] else None
        folder_id=integer(r["folder_id"]) if r["folder_id"] else None
        if item_id is not None and item_id not in known_items:
            raise ValueError("Материал не найден в справочнике.")
        if folder_id is not None and folder_id not in folder_ids:
            raise ValueError("Папка не найдена.")
        rows.append(dict(source_line=line,name=name,unit=clean(r["unit"],30),
                         quantity=number(r["quantity"],1000),price=number(r["price"],10000),
                         value=number(r["value"],100),action=action,item_id=item_id,
                         folder_id=folder_id,note=clean(r["note"],500),manual=line is None))
    kind=str(form.get("document_type") or "unknown")
    if kind not in inbox.TYPE_KEYS:
        raise ValueError("Неизвестный тип документа.")
    header={"document_type":kind,"counterparty":clean(form.get("counterparty"),200),
            "number":clean(form.get("number"),100),"date":inbox._iso_date(form.get("document_date")),
            "part":integer(form.get("part") or "1",1,MAX_PAGES),
            "note":clean(form.get("page_note"),1000)}
    checked=form.get("checked")=="1"
    empty=form.get("no_items")=="1"
    if empty and rows:
        raise ValueError("Страница без позиций не должна содержать строки.")
    if checked:
        if not rows and not empty:
            raise ValueError("Укажите позиции или подтвердите, что их нет.")
        if rows and (kind=="unknown" or not header["counterparty"] or not header["number"] or not header["date"]):
            raise ValueError("Для проверки страницы укажите реквизиты документа.")
        for r in rows:
            if not r["name"] or r["action"]=="unresolved":
                raise ValueError("Проверьте название и решение по каждой строке.")
            if r["action"]=="skip":
                if not r["note"]:
                    raise ValueError("Укажите причину исключения строки.")
                continue
            if not r["unit"] or r["quantity"] is None or Decimal(r["quantity"])<=0 or r["value"] is None:
                raise ValueError("Проверьте единицу, количество и итоговую стоимость с налогом.")
            if r["action"]=="existing" and (r["item_id"] is None
                    or norm(r["unit"])!=norm(known_items[r["item_id"]]["unit"])):
                raise ValueError("Выберите материал с совпадающей единицей учёта.")
            if r["action"]=="new" and (r["folder_id"] is None or r["item_id"] is not None or len(r["name"])>200):
                raise ValueError("Для нового материала выберите папку, название до 200 символов и не выбирайте существующий материал.")
    return {"header":header,"rows":rows,"checked":checked,"no_items":empty}


def register(app,db,permission_required,require_csrf,csrf_token,can_manage):
    base="/accounting/medical-supplies/incoming/<int:doc_id>/ocr"
    @app.get(base)
    @permission_required("accounting")
    def medical_supplies_ocr_page(doc_id):
        try:
            row,_,state=document(db(),doc_id)
            page=integer(request.args.get("p") or "1",1,MAX_PAGES)
            if "p" not in request.args:
                # A covering agreement may precede the goods. Open the first
                # usable page without changing the owner's explicit selection.
                for number in range(1, (state.get("page_count") or 0)+1):
                    saved_page=state["pages"].get(str(number))
                    if saved_page is None:
                        continue
                    proposal=derive_page(saved_page)
                    if proposal.get("rows",proposal["candidates"]):
                        page=number
                        break
            if state["page_count"] and page>state["page_count"]:
                abort(404)
            data=state["pages"].get(str(page))
            items=catalog(db())
            folders=[dict(x) for x in db().execute("""SELECT f.id,f.name,p.name AS parent
                     FROM med_supply_folders f LEFT JOIN med_supply_folders p ON p.id=f.parent_id
                     ORDER BY COALESCE(p.name,f.name),f.name""")]
            rows=[]
            header={}
            if data:
                data=derive_page(data)
                rows=[dict(r) for r in data.get("rows",data["candidates"])]
                for r in rows:
                    r["matches"]=suggestions(r["name"],r["unit"],items)
                header=data.get("header",{})
            response=make_response(render_template("medical_supplies_ocr.html",title="Разбор документа — Медрасходка",
                doc=row,state=state,page=page,data=data,rows=rows,header=header,
                items=items,folders=folders,types=inbox.TYPES,csrf=csrf_token(),can_manage=can_manage(),
                saved=request.args.get("saved")=="1"))
            response.headers["Cache-Control"]="private, no-store"
            return response
        except LookupError:
            abort(404)
        except ValueError as exc:
            return str(exc),400

    @app.post(base+"/page")
    @permission_required("accounting")
    def medical_supplies_ocr_run(doc_id):
        if not can_manage():
            abort(403)
        require_csrf()
        try:
            page=integer(request.form.get("page"),1,MAX_PAGES)
            version=integer(request.form.get("version"))
            conn=db()
            row,_,state=document(conn,doc_id,raw=True)
            if version!=row["version"]:
                raise inbox.Conflict("Карточка изменилась. Обновите страницу.")
            count=state.get("page_count")
            if count and page>count:
                raise ValueError("Такой страницы нет.")
            if str(page) in state["pages"]:
                previous=state["pages"][str(page)]
                derived=derive_page(previous)
                if derived is previous:
                    return jsonify(version=version,page_count=count,stored=len(state["pages"]),cached=True)
                def refresh(current):
                    # Only automatic proposals change. Reviewed/manual rows,
                    # identity, flags and original OCR evidence stay exact.
                    target=current["pages"][str(page)]
                    target["candidates"]=derived["candidates"]
                    target["extractor_version"]=EXTRACTOR_VERSION
                new_version=store(conn,doc_id,version,row["sha256"],refresh,g.user["id"],"ocr_page",
                    {"page":page,"words":len(derived["words"]),
                     "candidates":len(derived["candidates"]),"extraction_refreshed":True})
                return jsonify(version=new_version,page_count=count,stored=len(state["pages"]),cached=False)
            import app as app_core
            count,parsed=recognize_pdf_page(row["original_bytes"],page,app_core.DB_PATH.parent)
            def mutate(current):
                if current.get("page_count") not in (None,count):
                    raise inbox.Conflict("Количество страниц изменилось.")
                current["page_count"]=count
                if str(page) in current["pages"]:
                    raise inbox.Conflict("Страница уже распознана.")
                current["pages"][str(page)]=parsed
            new_version=store(conn,doc_id,version,row["sha256"],mutate,g.user["id"],"ocr_page",
                              {"page":page,"words":len(parsed["words"]),"candidates":len(parsed["candidates"])})
            return jsonify(version=new_version,page_count=count,stored=len(state["pages"])+1,cached=False)
        except LookupError:
            abort(404)
        except inbox.Conflict as exc:
            return jsonify(error=str(exc)),409
        except OCRUnavailable as exc:
            return jsonify(error=str(exc)),503
        except (ValueError,sqlite3.Error) as exc:
            return jsonify(error=str(exc) if isinstance(exc,ValueError) else "Не удалось сохранить результат."),400

    @app.post(base+"/save")
    @permission_required("accounting")
    def medical_supplies_ocr_save(doc_id):
        if not can_manage():
            abort(403)
        require_csrf()
        try:
            conn=db()
            row,_,state=document(conn,doc_id)
            version=integer(request.form.get("version"))
            if version!=row["version"]:
                raise inbox.Conflict("Карточка изменилась. Обновите страницу.")
            page=integer(request.form.get("page"),1,MAX_PAGES)
            data=state["pages"].get(str(page))
            if data is None:
                raise ValueError("Сначала распознайте страницу.")
            data=derive_page(data)
            folders=[dict(x) for x in conn.execute("SELECT id FROM med_supply_folders")]
            review=parse_review(request.form,data,catalog(conn),folders)
            def mutate(current):
                # AZ-264-01: identity belongs to the logical document, not
                # to its goods rows. Checked pages without goods must agree
                # as well, including when an already checked page is edited.
                if review["checked"]:
                    header=review["header"]
                    for key,other in current["pages"].items():
                        if key==str(page) or not other.get("checked"):
                            continue
                        previous=other.get("header",{})
                        if previous.get("part")==header["part"] and any(
                            previous.get(k)!=header.get(k) for k in ("document_type","counterparty","number","date")
                        ):
                            raise ValueError("У проверенных страниц одной части разные реквизиты. Уточните номер части.")
                target=current["pages"][str(page)]
                target["candidates"]=data["candidates"]
                target["extractor_version"]=EXTRACTOR_VERSION
                target.update(review)
            store(conn,doc_id,version,row["sha256"],mutate,g.user["id"],"ocr_review",
                  {"page":page,"rows":len(review["rows"]),"checked":review["checked"]})
            return redirect(url_for("medical_supplies_ocr_page",doc_id=doc_id,p=page,saved=1),code=303)
        except LookupError:
            abort(404)
        except inbox.Conflict as exc:
            return render_template("medical_supplies_error.html",title="Разбор документа",error=str(exc),
                back=url_for("medical_supplies_ocr_page",doc_id=doc_id)),409
        except ValueError as exc:
            return render_template("medical_supplies_error.html",title="Разбор документа",error=str(exc),
                back=url_for("medical_supplies_ocr_page",doc_id=doc_id)),400
