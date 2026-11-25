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
    if not words:
        return {"words":[],"lines":[],"candidates":[],"confidence":None}
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
    candidates=table_candidates(lines)
    return {"words":words,
            "lines":[{"id":l["id"],"text":l["text"]} for l in lines],
            "candidates":candidates,
            "confidence":round(sum(w[4] for w in words)/len(words),1)}


def table_candidates(lines):
    """Conservative geometry-based suggestions, never financial assertions.

    Require an identifiable header, a name column and a quantity column.
    Do not choose between multiple monetary columns (e.g. VAT variants).
    Unrecognized lines remain available as source evidence for manual entry.
    """
    result=[]
    columns=None
    def label(token):
        n=norm(token).replace(" ","")
        if n.startswith("наименован") or n=="name":
            return "name"
        if n.startswith("количеств") or n in ("колво","quantity","qty"):
            return "quantity"
        if n in ("ед","едизм","unit"):
            return "unit"
        if n in ("цена","price","тариф"):
            return "price"
        # Monetary headings are intentionally not interpreted as gross/net
        # if the document contains several possible total columns.
        if n in ("сумма","стоимость","total"):
            return "value"
        return None
    for i,line in enumerate(lines):
        tokens=line["tokens"]
        # Search a short multi-line header band; repeated headers reset mapping.
        if any(label(w[5])=="name" for w in tokens):
            band=[w for l in lines[i:i+4] for w in l["tokens"]]
            found={}
            for w in band:
                k=label(w[5])
                if k:
                    found.setdefault(k,[]).append(w)
            if len(found.get("name",[]))==1 and len(found.get("quantity",[]))==1:
                name,qty=found["name"][0],found["quantity"][0]
                if name[0]<qty[0]:
                    columns={}
                    for k,arr in found.items():
                        for index,w in enumerate(arr):
                            # Ambiguous repeated headings still delimit cells.
                            # Do not accidentally absorb them into a price column.
                            key=k if len(arr)==1 else f"ignore_{k}_{index}"
                            columns[key]=w[0]+w[2]/2
                    columns["name_start"]=max(0,name[0]-max(120,name[2]))
                    columns["header_end"]=max(w[1]+w[3] for k in found for w in found[k])
            continue
        if not columns or not tokens or min(w[1] for w in tokens)<=columns["header_end"]:
            continue
        line_norm=norm(line["text"])
        if re.match(r"^(итого|всего|total|к оплате|сумма к оплате|руководитель|главный бухгалтер)\b",line_norm):
            columns=None
            continue
        centers=sorted((v,k) for k,v in columns.items() if k not in ("name_start","header_end"))
        cells={k:[] for _,k in centers}
        for w in tokens:
            if w[0]+w[2]/2 < columns["name_start"]:
                continue
            _,key=min(centers,key=lambda c:abs(c[0]-(w[0]+w[2]/2)))
            cells[key].append(w[5])
        name=" ".join(cells.get("name",[])).strip()
        qty=maybe_number(" ".join(cells.get("quantity",[])),1000)
        # Name and quantity must be recognizable, and the line must not be
        # a total/footer. Wrapped and ambiguous lines require manual review.
        if not name or not re.search(r"[A-Za-zА-Яа-яЁё]",name) or not qty or Decimal(qty)<=0:
            continue
        unit=" ".join(cells.get("unit",[]))
        if len(unit)>30 or re.search(r"\d",unit):
            unit=""
        price=maybe_number(" ".join(cells.get("price",[])),10000)
        # No automatic gross line sum: the printed VAT-inclusive/exclusive
        # column is not known from the single word "сумма".
        if len(name)>500:
            continue
        result.append({"source_line":line["id"],"name":name,"unit":unit,
                       "quantity":qty,"price":price,"value":None,
                       "action":"unresolved","item_id":None,"folder_id":None,
                       "note":"","manual":False})
        if len(result)>=MAX_ROWS:
            break
    return result


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
                return jsonify(version=version,page_count=count,stored=len(state["pages"]),cached=True)
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
            folders=[dict(x) for x in conn.execute("SELECT id FROM med_supply_folders")]
            review=parse_review(request.form,data,catalog(conn),folders)
            def mutate(current):
                # Grouping pages into one logical document is an operator
                # decision. Checked pages of that group cannot contradict
                # each other's document identity.
                if review["checked"] and review["rows"]:
                    header=review["header"]
                    for key,other in current["pages"].items():
                        if key==str(page) or not other.get("checked") or not other.get("rows"):
                            continue
                        previous=other.get("header",{})
                        if previous.get("part")==header["part"] and any(
                            previous.get(k)!=header.get(k) for k in ("document_type","counterparty","number","date")
                        ):
                            raise ValueError("У проверенных страниц одной части разные реквизиты. Уточните номер части.")
                current["pages"][str(page)].update(review)
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
