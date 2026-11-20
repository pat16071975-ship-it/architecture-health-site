"""Target-led planning, isolated from variant 1 and all factual ledgers."""
import calendar
import copy
import hashlib
import json
import math
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import abort, current_app, g, jsonify, render_template, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import financial_model as v1

SCHEMA = '''
CREATE TABLE IF NOT EXISTS financial_model_v2_plan (
 year INTEGER PRIMARY KEY, version INTEGER NOT NULL,
 config TEXT NOT NULL, results TEXT NOT NULL, stale TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS financial_model_v2_history (
 id INTEGER PRIMARY KEY, year INTEGER NOT NULL, version INTEGER NOT NULL,
 actor_id INTEGER NOT NULL, changed_at TEXT NOT NULL, mode TEXT NOT NULL,
 before_json TEXT NOT NULL, after_json TEXT NOT NULL, UNIQUE(year,version)
);
'''
DIRECTIONS = {'medicine': 'Вся медицина', 'dentistry': 'Стоматология',
              'structure': 'Отделение структуры'}
RESULT_KEYS = ('revenue', 'total_cost', 'profit', 'official_extra_tax',
               'official_adjusted_profit', 'paid_patients', 'required_hours',
               'missing_fte', 'needed_marketing')


def init_schema(conn):
    conn.executescript(SCHEMA)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode()).hexdigest()


def total(values):
    values = list(values)
    return None if any(v is None for v in values) else sum(values)


def divide(numerator, denominator):
    if numerator == 0:
        return 0
    return numerator / denominator if numerator is not None and denominator else None


def people(value, check):
    n = divide(value, check)
    return math.ceil(n - 1e-10) if n is not None else None


def calculate(config):
    """Same goal drives demand and economics; supply never truncates the goal."""
    available = v1.calculate(config)
    result, official_growth = [], 0
    for i, supply in enumerate(available):
        p = {k: values[i] for k, values in config['months'].items()}
        revenue = p['target']
        clinic = revenue / (1 + p['other_revenue_rate'])
        weight = sum(r['weight'][i] for r in config['rooms'])
        productive = p['booking'] * p['attendance']
        rooms, warnings = [], []
        if not weight and clinic:
            warnings.append('Задайте распределение цели по направлениям и кабинетам.')
        for room, supported in zip(config['rooms'], supply['rooms']):
            target = clinic * room['weight'][i] / weight if weight else (0 if not clinic else None)
            hours = divide(target, room['rate'][i])
            schedule = divide(hours, productive)
            staff_fte = divide(schedule, p['staff_hours'])
            missing_hours = max(0, hours - supported['hours']) if hours is not None else None
            missing_schedule = divide(missing_hours, productive)
            missing_fte = divide(missing_schedule, p['staff_hours'])
            capacity_gap = max(0, schedule - supported['capacity']) if schedule is not None else None
            r = dict(supported, revenue=target, demand=hours, hours=hours,
                     required_schedule=schedule, required_fte=staff_fte,
                     available_hours=supported['hours'], available_revenue=supported['revenue'],
                     missing=missing_hours, missing_schedule=missing_schedule, fte=missing_fte,
                     capacity_gap=capacity_gap, load=divide(schedule, supported['capacity']))
            rooms.append(r)
            if target is not None and hours is None:
                warnings.append(f"Каб. {r['id']}: задайте положительную выручку за состоявшийся час.")
            if schedule is None and hours is not None:
                warnings.append(f"Каб. {r['id']}: для потребности в графике задайте запись и явку больше нуля.")
            if capacity_gap:
                warnings.append(f"Каб. {r['id']}: нужно добавить {capacity_gap:.1f} ч ёмкости или изменить распределение цели.")
        if any(r['required_fte'] is None for r in rooms) and not p['staff_hours']:
            warnings.append('Для расчёта ставок задайте положительную месячную норму часов.')
        warnings.extend(w for w in supply['warnings'] if w.startswith('Не назначен кабинет:')
                        or 'график превышает ёмкость' in w)
        hours = total(r['hours'] for r in rooms)
        schedule = total(r['required_schedule'] for r in rooms)
        visits = divide(None if hours is None else hours * 60, p['visit_minutes'])
        new = visits * p['new_share'] if visits is not None else None
        leads = divide(new, p['lead_conversion'] * p['attendance'])
        marketing = leads * p['lead_cost'] if leads is not None else None
        budget = p['marketing_fixed'] if p['marketing_fixed'] is not None else revenue * p['marketing_rate']
        band = 'low' if revenue < 12000000 else 'mid' if revenue < 18000000 else 'high'
        wage = clinic * (p['doctor_pay'] + p['lab_pay']) + sum(
            p[f'{group}_{band}'] for group in ('admin', 'assist', 'management', 'marketing_pay'))
        cost = dict(wage=wage, payroll_tax=wage * (p['payroll_tax'] or 0),
                    medical=clinic * p['medical_rate'], lab=clinic * p['lab_cost_rate'],
                    rent=p['rent'], marketing=marketing, it=p['it'], outsourcing=p['outsourcing'],
                    other_cost=p['other_cost'], reserve=revenue * p['reserve_rate'])
        base_cost = total(cost.values())
        base_before = revenue - base_cost if base_cost is not None else None
        def tax_on(profit):
            return max(max(profit, 0) * p['tax_rate'], revenue * p['minimum_tax']) if profit is not None else None
        base_tax = tax_on(base_before)
        base_profit = base_before - base_tax if base_tax is not None else None
        official_growth += p['official_step_pct']
        base, burden = p['official_base_pct'], p['official_burden_pct']
        share = min(100, base + official_growth) if base is not None else None
        extra_wage = wage * (share - base) / 100 if base is not None else None
        extra_tax = (extra_wage * burden / 100 if extra_wage is not None and burden is not None
                     else 0 if extra_wage == 0 else None)
        full_cost = total([base_cost, extra_tax])
        before = revenue - full_cost if full_cost is not None else None
        tax = tax_on(before)
        profit = before - tax if tax is not None else None
        if extra_tax is None:
            warnings.append('Для итоговой прибыли заполните исходную официальную долю ФОТ и ставку дополнительных взносов.')
        if marketing is None:
            warnings.append('Потребность в маркетинге неизвестна: проверьте часы, длительность приёма, конверсию и явку.')
        direction_revenue = {'medicine': clinic, **{
            direction: total(r['revenue'] for r in rooms if r['direction'] == direction)
            for direction in ('dentistry', 'structure')}}
        payers = {key: people(amount, config['payer_checks'][key][i])
                  for key, amount in direction_revenue.items()}
        for key, number in payers.items():
            if number is None:
                warnings.append(DIRECTIONS[key] + ': задайте средний чек оплатившего пациента.')
        result.append(dict(
            month=i+1, target=revenue, revenue=revenue, dentistry=direction_revenue['dentistry'],
            structure=direction_revenue['structure'], other_revenue=revenue-clinic,
            available_revenue=supply['revenue'], revenue_to_add=max(0, revenue-supply['revenue']),
            capacity=supply['capacity'], staffed=supply['staffed'], booked=supply['booked'],
            hours=hours, required_hours=hours, required_schedule=schedule,
            available_hours=supply['hours'], missing_hours=total(r['missing'] for r in rooms),
            required_fte=total(r['required_fte'] for r in rooms),
            missing_fte=total(r['fte'] for r in rooms), capacity_gap=total(r['capacity_gap'] for r in rooms),
            load=divide(schedule, supply['capacity']), visits=visits, new=new, leads=leads,
            paid_patients=payers['medicine'], paid_dentistry=payers['dentistry'], paid_structure=payers['structure'],
            payer_checks={k: v[i] for k, v in config['payer_checks'].items()},
            payer_sources={k: v[i] for k, v in config['payer_sources'].items()},
            needed_marketing=marketing, marketing_budget=budget,
            marketing_to_add=max(0, marketing-budget) if marketing is not None else None,
            **cost, known_cost=sum(x for x in cost.values() if x is not None)+(extra_tax or 0),
            base_total_cost=base_cost, base_profit=base_profit, base_tax=base_tax,
            total_cost=full_cost, profit_before=before, tax=tax, profit=profit,
            margin=divide(profit, revenue), official_adjusted_profit=profit,
            official_adjusted_margin=divide(profit, revenue),
            official_share=share/100 if share is not None else None,
            official_base_wage=wage*base/100 if base is not None else None,
            official_extra_wage=extra_wage, official_extra_tax=extra_tax,
            official_full_wage=wage*share/100 if share is not None else None,
            starts=supply['starts'], ramping=supply['ramping'],
            base_contributions_in_reserve=p['payroll_tax'] is None,
            hiring_context=supply['hiring_context'], rooms=rooms, hires=supply['hires'], warnings=warnings))
    return result


def read_state(conn):
    row = conn.execute('SELECT * FROM financial_model_v2_plan WHERE year=2027').fetchone()
    if row:
        return dict(version=row['version'], config=json.loads(row['config']),
                    results=json.loads(row['results']), stale=json.loads(row['stale']))
    first = v1.read_state(conn)
    c = copy.deepcopy(first['config'])
    c.update(payer_checks={k: [None]*12 for k in DIRECTIONS},
             payer_sources={k: [None]*12 for k in DIRECTIONS},
             variant=2, origin_version=first['version'])
    initialization = dict(status='missing')
    try:
        refs = references(conn)
    except (ValueError, sqlite3.DatabaseError, OSError):
        # A source failure must not make the whole planning screen unavailable,
        # nor substitute a fabricated check. The UI shows an explicit reason.
        refs = []
        initialization = dict(status='error')
    if refs:
        source = max(refs, key=lambda r: r['end'])
        apply_payer_source(c, source, range(12))
        initialization = dict(status='source', month=source['month'])
    return dict(version=0, config=c, results=calculate(c), stale=[],
                payer_initialization=initialization)


def apply_payer_source(config, source, indices):
    """Freeze the same source for all directions, including unknown checks."""
    for key in DIRECTIONS:
        for i in indices:
            config['payer_checks'][key][i] = source['values'][key]['check']
            config['payer_sources'][key][i] = dict(kind='report', month=source['month'],
                digest=source['digest'], start=source['start'], end=source['end'],
                **source['values'][key], evidence=copy.deepcopy(source['evidence']))


def references(conn, today=None):
    """Full calendar-month paid export + unique payers from the SAME selected parts.

    Return aggregates and hashes only. No patient tokens leave this function.
    Historical coverage declarations are reused, never inferred from first service day.
    """
    import paid_period_confirmation as periods
    import report_storage as reports
    today = today or datetime.now(ZoneInfo('Asia/Novosibirsk')).date()
    dbpath = conn.execute('PRAGMA database_list').fetchone()[2]
    confirmations = periods.load_config(Path(dbpath)) if dbpath else []
    sources = periods.read_sources(conn, confirmations)
    mappings, ignored = reports._management_doctor_provider_mappings(conn)
    unique = {(s['from'], s['to']): s for s in sources}
    intervals = []
    for s in unique.values():
        part = reports._paid_patient_interval(s['payload'], s['from'], s['to'], mappings, ignored)
        if part is None:
            continue
        amounts = {'dent': 0, 'structure': 0}
        valid = True
        for provider, values in s['payload']['providers'].items():
            if provider in ignored or provider in mappings['lab']:
                continue
            direction = next((k for k in amounts if provider in mappings[k]), None)
            try:
                value = values['paid']
                if direction is None or value is None or isinstance(value, bool):
                    raise ValueError()
                value = float(value)
                if not math.isfinite(value):
                    raise ValueError()
                amounts[direction] += value
            except (TypeError, ValueError, KeyError, OverflowError):
                valid = False
                break
        if valid:
            intervals.append(dict(part, amounts=amounts, evidence={
                k: s[k] for k in ('kind', 'sourceFrom', 'sourceTo', 'payloadSha256', 'sourceSha256', 'importedAt')}))
    result = []
    for month in sorted({s['to'][:7] for s in intervals}):
        start, end = periods.month_bounds(month)
        if date.fromisoformat(end) >= today:
            continue
        parts = reports._cover_paid_patient_range(intervals, start, end)
        if not parts:
            continue
        counts = reports._combine_paid_patient_intervals(parts)
        amounts = {k: sum(p['amounts'][k] for p in parts) for k in ('dent', 'structure')}
        amounts['medicine'] = sum(amounts.values())
        values = {}
        for key, source_key in [('medicine','medicine'), ('dentistry','dent'), ('structure','structure')]:
            n, value = counts[source_key], amounts[source_key]
            values[key] = dict(revenue=value, patients=n, check=value/n if n and value > 0 else None)
        item = dict(month=month, start=start, end=end, values=values,
                    evidence=[p['evidence'] for p in parts])
        item['digest'] = digest(item)
        result.append(item)
    return result


def candidate(old, change, refs=(), validate_ramp=True):
    if not isinstance(change, dict):
        raise ValueError('Некорректная правка.')
    group = change.get('group')
    if group == 'batch':
        edits = change.get('changes')
        if not isinstance(edits, list) or not 1 <= len(edits) <= 100:
            raise ValueError('Выберите от 1 до 100 изменений.')
        working = copy.deepcopy(old)
        indices = set()
        for edit in edits:
            if not isinstance(edit, dict) or edit.get('group') in ('batch', 'refresh'):
                raise ValueError('Некорректная группа изменений.')
            c, _, affected = candidate(working, edit, refs, False)
            working['config'] = c
            indices.update(affected)
        c = working['config']
        if c['ramp'] != sorted(c['ramp']):
            raise ValueError('Загрузка адаптации не должна уменьшаться.')
        return c, min(indices)+1, sorted(indices)
    if group not in ('payer_check', 'payer_source'):
        return v1.candidate(old, change, validate_ramp)
    start = int(change.get('from', 0))
    if not 1 <= start <= 12 or change.get('scope') not in ('month', 'future'):
        raise ValueError('Выберите месяц и период применения.')
    indices = list(range(start-1,12)) if change['scope']=='future' else [start-1]
    c = copy.deepcopy(old['config'])
    if group == 'payer_check':
        key = change.get('key')
        if key not in DIRECTIONS:
            raise ValueError('Неизвестное направление.')
        value = v1.numeric(change.get('value'), 1e8, nullable=True)
        for i in indices:
            c['payer_checks'][key][i] = value
            c['payer_sources'][key][i] = {'kind':'manual'}
    else:
        source = next((r for r in refs if r['month'] == change.get('month')
                       and r['digest'] == change.get('digest')), None)
        if source is None:
            raise ValueError('Исходные данные месяца изменились или неполны. Обновите список.')
        # All three directions deliberately replace the selected period together,
        # including unknown checks, never retain an unrelated old value silently.
        apply_payer_source(c, source, indices)
    return c, start, indices


def transition(old, change, mode, refs=()):
    if mode not in ('keep', 'recalculate'):
        raise ValueError('Выберите действие с результатами.')
    if change.get('group') == 'refresh':
        if mode != 'recalculate':
            raise ValueError('Обновление требует пересчёта.')
        c, start, indices = copy.deepcopy(old['config']), 1, list(range(12))
    else:
        c, start, indices = candidate(old, change, refs)
    fresh = calculate(c)
    results, stale = copy.deepcopy(old['results']), set(old['stale'])
    if mode == 'keep':
        stale.update(i+1 for i in indices)
    else:
        for i in indices:
            results[i] = fresh[i]
            stale.discard(i+1)
    return dict(version=old['version']+1, config=c, results=results, stale=sorted(stale)), start


def uses_reference(change):
    return change.get('group') == 'payer_source' or (change.get('group') == 'batch' and
           any(isinstance(c, dict) and uses_reference(c) for c in change.get('changes', [])))


def commit_change(conn, payload, actor):
    conn.execute('BEGIN IMMEDIATE')
    try:
        old = read_state(conn)
        if payload['version'] != old['version'] or payload['state_digest'] != digest(old):
            raise RuntimeError('Модель изменилась. Обновите страницу и повторите просмотр.')
        refs = references(conn) if uses_reference(payload['change']) else ()
        new, _ = transition(old, payload['change'], payload['mode'], refs)
        conn.execute('INSERT INTO financial_model_v2_plan VALUES(2027,?,?,?,?) '
                     'ON CONFLICT(year) DO UPDATE SET version=excluded.version,config=excluded.config,'
                     'results=excluded.results,stale=excluded.stale',
                     (new['version'], json.dumps(new['config'],ensure_ascii=False),
                      json.dumps(new['results'],ensure_ascii=False), json.dumps(new['stale'])))
        conn.execute('INSERT INTO financial_model_v2_history(year,version,actor_id,changed_at,mode,'
                     'before_json,after_json) VALUES(2027,?,?,?,?,?,?)',
                     (new['version'],actor,datetime.now(timezone.utc).isoformat(),payload['mode'],
                      json.dumps(old,ensure_ascii=False),json.dumps(new,ensure_ascii=False)))
        conn.commit()
        return new
    except Exception:
        conn.rollback()
        raise


def history_label(path, config):
    parts = path.strip('/').split('/')
    if len(parts) == 3 and parts[0] == 'months':
        return v1.PARAMS[parts[1]][0] + ' · ' + v1.MONTHS[int(parts[2])-1]
    if len(parts) == 4 and parts[0] == 'rooms':
        r = config['rooms'][int(parts[1])-1]
        return 'Каб. ' + r['id'] + ' · ' + v1.ROOM_PARAMS[parts[2]][0] + ' · ' + v1.MONTHS[int(parts[3])-1]
    if len(parts) == 3 and parts[0] == 'hires':
        h = config['hires'][int(parts[1])-1]
        return h['name'] + ' · ' + {'start':'Выход','room':'Кабинет','count':'Ставки'}.get(parts[2],parts[2])
    if len(parts) == 2 and parts[0] == 'ramp':
        return 'Адаптация · месяц ' + parts[1]
    if len(parts) >= 3 and parts[0] in ('payer_checks','payer_sources'):
        label = ('Средний чек' if parts[0]=='payer_checks' else 'Источник среднего чека')
        return label + ': ' + DIRECTIONS[parts[1]] + ' · ' + v1.MONTHS[int(parts[2])-1]
    return path


def register(app, db, permission_required, require_csrf, csrf_token, user_permissions):
    def guard():
        if 'financial_model_edit' not in user_permissions(g.user):
            abort(403)
    def serializer():
        return URLSafeTimedSerializer(current_app.secret_key, salt='financial-model-preview-v2')
    @app.get('/reports/financial-model/v2/')
    @permission_required('reports')
    def financial_model_v2_page():
        state = read_state(db())
        return render_template('financial_model_v2.html', state=state, months=v1.MONTHS,
            params=v1.PARAMS, room_params=v1.ROOM_PARAMS, directions=DIRECTIONS,
            needs=v1.hiring_needs(state['results']),
            can_edit='financial_model_edit' in user_permissions(g.user), csrf=csrf_token())
    @app.get('/reports/financial-model/v2/references')
    @permission_required('reports')
    def financial_model_v2_references():
        try:
            return jsonify(references=references(db()))
        except (ValueError, sqlite3.DatabaseError):
            return jsonify(error='Не удалось проверить полноту исходных выгрузок.'), 400
    @app.post('/reports/financial-model/v2/preview')
    @permission_required('reports')
    def financial_model_v2_preview():
        guard()
        require_csrf()
        try:
            old = read_state(db())
            if int(request.form.get('version',-1)) != old['version']:
                return jsonify(error='Модель изменилась. Обновите страницу.'), 409
            change = json.loads(request.form.get('change','{}'))
            if not isinstance(change, dict):
                raise ValueError('Некорректная правка.')
            mode = request.form.get('mode')
            refs = references(db()) if uses_reference(change) else ()
            new, start = transition(old, change, mode, refs)
            payload = dict(version=old['version'], state_digest=digest(old), change=change,
                           mode=mode, actor=g.user['id'])
            return jsonify(token=serializer().dumps(payload), from_month=start,
                before=[{k:r[k] for k in RESULT_KEYS} for r in old['results']],
                after=[{k:r[k] for k in RESULT_KEYS} for r in new['results']])
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as e:
            return jsonify(error=str(e) if isinstance(e, ValueError) else 'Проверьте значения и период.'), 400
    @app.post('/reports/financial-model/v2/save')
    @permission_required('reports')
    def financial_model_v2_save():
        guard()
        require_csrf()
        try:
            payload = serializer().loads(request.form.get('token',''),max_age=1800)
            if payload['actor'] != g.user['id']:
                abort(403)
            state = commit_change(db(), payload, g.user['id'])
            return jsonify(version=state['version'])
        except (BadSignature, SignatureExpired):
            return jsonify(error='Подтверждение недействительно. Повторите просмотр.'), 400
        except (ValueError, TypeError, KeyError):
            return jsonify(error='Данные изменились или недействительны. Повторите просмотр.'), 400
        except RuntimeError as e:
            return jsonify(error=str(e)), 409
    @app.get('/reports/financial-model/v2/history')
    @permission_required('reports')
    def financial_model_v2_history():
        rows = db().execute('SELECT * FROM financial_model_v2_history WHERE year=2027 ORDER BY version DESC LIMIT 100').fetchall()
        history = []
        for r in rows:
            before, after = json.loads(r['before_json'])['config'], json.loads(r['after_json'])['config']
            changes = []
            def walk(a,b,path=''):
                if path.startswith('/payer_sources/') and len(path.strip('/').split('/')) == 3:
                    def source_label(s):
                        return ('Полный месяц ' + s['month'] if s.get('kind') == 'report' else 'Плановый ввод') if s else None
                    if a != b:
                        changes.append(dict(field=history_label(path,after),before=source_label(a),after=source_label(b)))
                    return
                if isinstance(a,dict) and isinstance(b,dict):
                    for k in b:
                        if k not in ('evidence','digest'): walk(a.get(k),b[k],path+'/'+str(k))
                elif isinstance(a,list) and isinstance(b,list) and len(a)==len(b):
                    for i,(x,y) in enumerate(zip(a,b)): walk(x,y,path+'/'+str(i+1))
                elif a != b:
                    changes.append(dict(field=history_label(path,after),before=a,after=b))
            walk(before, after)
            history.append(dict(version=r['version'],actor_id=r['actor_id'],changed_at=r['changed_at'],
                                mode=r['mode'],changes=changes))
        return jsonify(history=history)
