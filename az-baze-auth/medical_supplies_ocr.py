"""Local OCR of saved PDF scans; editable evidence-backed drafts, not receipts.

No network/API key, no new schema. Original bytes stay in the inbox.
One bounded page per POST; the browser may request subsequent pages.
"""
import csv
import fcntl
import hashlib
import io
import json
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
EXTRACTOR_VERSION = 2
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
    if text in ("ед", "unit") or text.startswith(("едизм", "единиц")):
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
        if center < columns["name_start"]:
            continue
        ordinal = anchors.get("ordinal")
        if (ordinal is not None and center < anchors["name"][0]
                and re.fullmatch(r"\d+[.)]?", word[5])
                and abs(center-ordinal[0]-ordinal[2]/2) <= max(60, ordinal[2]*2)):
            cells["ordinal"].append(word[5])
            continue
        # A broad name column must not be cut at the midpoint of its short
        # heading and the next column: that drops long printed product names.
        if center < name_end:
            key = "name"
        else:
            key = min((k for k in anchors if k != "name"),
                      key=lambda k:abs(anchors[k][0] + anchors[k][2] / 2 - center))
        cells[key].append(word[5])
    return {key:" ".join(values).strip() for key, values in cells.items()}


def _table_header(lines, start):
    found = {}
    band = []
    top = min(w[1] for w in lines[start]["tokens"])
    height = median(w[3] for w in lines[start]["tokens"])
    for line in lines[start:start+10]:
        if min(w[1] for w in line["tokens"]) - top > max(160, height * 9):
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
            if numeric and letters:
                break
        band.append(line)
        for word in line["tokens"]:
            label = _column_label(word[5])
            if label:
                found.setdefault(label, []).append(word)
    if len(found.get("name", [])) != 1 or len(found.get("quantity", [])) != 1:
        return None
    name, quantity = found["name"][0], found["quantity"][0]
    if name[0] >= quantity[0]:
        return None
    anchors = {}
    for label, words in found.items():
        for i, word in enumerate(words):
            anchors[label if len(words) == 1 else f"{label}_{i}"] = word
    right = [w[0] for key, w in anchors.items() if key != "name" and w[0] > name[0]]
    columns = {"anchors":anchors,
               "name_start":max(0, name[0]-max(120, name[2])),
               "name_end":min(right)-8,
               "header_end":max(w[1]+w[3] for line in band for w in line["tokens"]),
               "value_key":None, "value_basis":None}
    heading = _cells([w for line in band for w in line["tokens"]], columns)
    money = [key for key in anchors if key == "value" or key.startswith("value_")]
    gross = []
    excluded = set()
    for key in money:
        text = norm(heading[key])
        net = bool(re.search(r"\bбез\s*(?:ндс|налога)\b", text))
        tax = bool(re.search(r"\bсумма\s*(?:налога|ндс)\b", text))
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


def table_candidates(lines):
    """Printed goods proposals with evidence; never infer money or quantities."""
    result, segment = [], []
    columns = None

    def finish():
        anchors = [part for part in segment if part["quantity"] is not None]
        names = {part["line"]["id"]:[part] for part in anchors}
        pending = [part for part in segment if part["quantity"] is None and part["name_only"]]
        # Join adjacent printed name fragments to a uniquely closest goods
        # row, including three-line names. Never cross a total/header boundary.
        while pending and anchors:
            remaining = []
            for part in pending:
                distances = sorted((abs(part["y"]-row["y"]), row["line"]["id"], row)
                                   for row in anchors)
                distance, ident, row = distances[0]
                nearest_piece = min(abs(part["y"]-piece["y"]) for piece in names[ident])
                if (nearest_piece <= max(part["height"], row["height"])*1.8
                        and (len(distances) == 1 or distance < distances[1][0])):
                    names[ident].append(part)
                else:
                    remaining.append(part)
            if len(remaining) == len(pending):
                break
            pending = remaining
        for part in anchors:
            pieces = sorted(names[part["line"]["id"]], key=lambda item:item["y"])
            name = " ".join(item["cells"].get("name", "") for item in pieces).strip()
            if not re.search(r"[A-Za-zА-Яа-яЁё]{2}", name) or len(name) > 500:
                continue
            cells = part["cells"]
            unit = cells.get("unit", "")
            if len(unit) > 30 or re.search(r"\d", unit):
                unit = ""
            value = maybe_number(cells.get(columns["value_key"], ""), 100)
            result.append({"source_line":part["line"]["id"],
                           "source_lines":[item["line"]["id"] for item in pieces],
                           "name":name, "unit":unit, "quantity":part["quantity"],
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
        name_only = bool(cells.get("name") and all(not value for key, value in cells.items() if key != "name"))
        segment.append({"line":line, "cells":cells, "quantity":quantity, "name_only":name_only,
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
