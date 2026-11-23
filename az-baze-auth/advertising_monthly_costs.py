"""Monthly fixed costs and owner confirmation; Direct is never written here."""
import calendar
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

KEY = 'az-advertising-monthly-costs-v1'
# Bump this when the meaning of a monthly fixed sum changes; never silently
# reuse a prior confirmation just because its raw rows did not change.
CONFIRMATION_CALC_VERSION = 'instagram-campaign-reconcile-v3'


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def bounds(month):
    if not re.fullmatch(r'\d{4}-\d{2}', str(month)):
        raise ValueError('Выберите месяц и год.')
    first = date.fromisoformat(month + '-01')
    if not 2000 <= first.year <= 2100:
        raise ValueError('Недопустимый год.')
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def amount(value):
    if isinstance(value, bool) or value is None or str(value).strip() == '':
        raise ValueError('Укажите сумму; настоящий ноль допустим.')
    try:
        result = Decimal(str(value).replace(' ', '').replace(',', '.'))
        if not result.is_finite() or result < 0 or result > Decimal('100000000000'):
            raise ValueError('Недопустимая сумма.')
        return float(result.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise ValueError('Недопустимая сумма.') from exc


def load(conn):
    # No lazy schema initialization and no writes on a report GET.
    try:
        row = conn.execute('SELECT payload FROM report_blobs WHERE key=?', (KEY,)).fetchone()
    except sqlite3.OperationalError as exc:
        if 'no such table: report_blobs' not in str(exc):
            raise
        row = None
    return json.loads(row['payload']) if row else {'version': 1, 'entries': {}, 'confirmed': {}, 'history': []}


def legacy_entries(core, store):
    result = []
    for level, bucket in [('source', 'sources'), ('campaign', 'campaigns')]:
        for key, periods in store['periods'][bucket].items():
            meta = store[bucket][key]
            source = key if level == 'source' else meta['source_key']
            if level == 'campaign' and source == 'yandex_ads':
                continue  # Historical Direct overrides are not payment evidence.
            for raw in periods:
                value = raw.get('contractors_payment' if source == 'yandex_ads' else 'advertising_payment')
                if value is None:
                    continue
                result.append({'id': 'legacy:' + digest([level, key, raw]),
                    'month': raw['from'][:7] if raw['from'][:7] == raw['to'][:7] else None,
                    'from': raw['from'], 'to': raw['to'], 'amount': value,
                    'source': source, 'level': level, 'key': key,
                    'label': ('Подрядчик Яндекс-рекламы' if source == 'yandex_ads' else meta.get('name') or meta.get('campaign_name') or key),
                    'comment': '', 'legacy': True})
    return result


def records(core, store, state):
    return legacy_entries(core, store) + list(state['entries'].values())


def selected_records(rows, month):
    first, last = bounds(month)
    return [r for r in rows if r.get('month') == month or (
        r.get('month') is None and r.get('from', '9999') <= last.isoformat() and r.get('to', '') >= first.isoformat())]


def _instagram_campaign_identity(row, store):
    """Only owner-confirmed legacy Instagram names, never arbitrary campaign labels."""
    label = str(row.get('label') or '').strip().casefold()
    if row.get('level') != 'campaign':
        return LEGACY_INSTAGRAM_PAYMENT_NAMES.get(label)
    meta = (store.get('campaigns') or {}).get(row.get('key') or '') if store else None
    name = str((meta or {}).get('campaign_name') or row.get('label') or '').strip().casefold()
    return LEGACY_INSTAGRAM_PAYMENT_NAMES.get(name)


def fixed_total(rows, store=None):
    if any(r.get('month') is None for r in rows):
        return None
    # Each source is independent. An ordinary source-level payment is a
    # parent budget and suppresses ALL child detail, as before (A-COST-06).
    # A specifically labelled historical Instagram *campaign* payment stored
    # at source level is different: it suppresses ONLY the same campaign's
    # duplicated child payment, not a different Instagram campaign.
    by_source = {}
    for row in rows:
        by_source.setdefault(row['source'], []).append(row)
    result = Decimal(0)
    for source, subset in by_source.items():
        parents = [r for r in subset if r.get('level') != 'campaign']
        children = [r for r in subset if r.get('level') == 'campaign']
        if source != 'instagram' or any(
            _instagram_campaign_identity(r, store) is None for r in parents
        ):
            chosen = parents if parents else children
            result += sum((Decimal(str(r['amount'])) for r in chosen), Decimal(0))
            continue
        grouped = {}
        for row in subset:
            identity = _instagram_campaign_identity(row, store)
            # Independent, unrecognized child campaigns are still additive.
            identity = identity or ('key', row.get('key') or row.get('id'))
            bucket = grouped.setdefault(identity, {'source': [], 'campaign': []})
            kind = 'campaign' if row.get('level') == 'campaign' else 'source'
            bucket[kind].append(row)
        for bucket in grouped.values():
            # AZ-254-03: within one canonical Instagram campaign, NEW monthly
            # evidence always wins over old archived metadata, regardless
            # whether a legacy-named monthly payment was saved at source or
            # campaign level. A modern campaign payment outranks its matching
            # modern source-level duplicate, but an archived 60k campaign
            # must never suppress a newer 100k source-level clinic payment.
            new_campaign = [r for r in bucket['campaign'] if not r.get('legacy')]
            new_source = [r for r in bucket['source'] if not r.get('legacy')]
            chosen = (new_campaign or new_source
                      or bucket['campaign'] or bucket['source'])
            if chosen:
                # The modern monthly ledger supersedes archival history for
                # this same campaign/month; do not append it in the source.
                newer_months = {
                    r['month'] for r in chosen
                    if not r.get('legacy') and r.get('month')
                }
                chosen = [
                    r for r in chosen
                    if not r.get('legacy') or r.get('month') not in newer_months
                ]
                legacy_intervals = {}
                deduped = []
                for row in chosen:
                    if row.get('legacy'):
                        interval = (row.get('from'), row.get('to'))
                        previous = legacy_intervals.get(interval)
                        if previous is not None:
                            if Decimal(str(previous['amount'])) != Decimal(str(row['amount'])):
                                # Two different historical amounts refer to
                                # the same canonical campaign/date interval.
                                # Do not silently add or choose one: unknown.
                                return None
                            continue
                        legacy_intervals[interval] = row
                    deduped.append(row)
                chosen = deduped
            result += sum((Decimal(str(r['amount'])) for r in chosen), Decimal(0))
    return float(round(result, 2))


def valid_confirmation(core, store, state, month):
    """A confirmation covers BOTH rows and the calculation visible at approval.

    Legacy confirmations have no version. Their saved fixed and adjustment
    reconstruct the *original automatic basis*, so unaffected old months
    remain approved, while changed calculations require owner reapproval.
    A previous manual adjustment stays in the untouched stored confirmation
    and its audit history; it is never silently rebased or erased.
    """
    confirmed = state['confirmed'].get(month)
    if not confirmed:
        return None
    rows = selected_records(records(core, store, state), month)
    if confirmed.get('records_digest') != digest(rows):
        return None
    new_base = fixed_total(rows, store)
    if new_base is None:
        return None
    try:
        if 'calculation_version' in confirmed:
            if confirmed['calculation_version'] != CONFIRMATION_CALC_VERSION:
                return None
            old_base = Decimal(str(confirmed['base_fixed']))
        else:
            # Existing historical confirmations: fixed = old_base + adjustment.
            old_base = (Decimal(str(confirmed['fixed'])) -
                        Decimal(str(confirmed['adjustment'])))
        if not old_base.is_finite():
            return None
        if old_base.quantize(Decimal('.01')) != Decimal(str(new_base)).quantize(Decimal('.01')):
            return None
        return confirmed
    except (KeyError, TypeError, InvalidOperation, ValueError):
        return None


def source_period(core, conn, store, key, first, last, state=None):
    """Prorate saved source payments independently of global approval.

    Missing months remain unknown until explicitly confirmed. The Direct
    contractor keeps its existing confirmation contract; API spend is separate.
    """
    state = state if state is not None else load(conn)
    all_rows = records(core, store, state)
    if key != "yandex_ads" and not any(r["source"] == key for r in all_rows):
        return None
    total = Decimal(0)
    cursor = first
    while cursor <= last:
        month = cursor.strftime('%Y-%m')
        end = min(bounds(month)[1], last)
        confirmed = valid_confirmation(core, store, state, month)
        rows = [r for r in selected_records(all_rows, month) if r['source'] == key]
        if (key == 'yandex_ads' or not rows) and not confirmed:
            return None
        fixed = fixed_total(rows, store)
        if fixed is None:
            return None  # A cross-month legacy payment needs an explicit month.
        total += apportioned(fixed, cursor, end)
        cursor = end + timedelta(days=1)
    return float(total)


# Explicit compatibility for two owner-identified legacy Instagram payments.
# This affects only campaign attribution; original rows, approval digest and
# source-level totals remain unchanged. Free-form labels in other sources are
# NEVER treated as campaign identities.
LEGACY_INSTAGRAM_PAYMENT_NAMES = {
    'зубачёв': 'Зубачев',
    'зубачев': 'Зубачев',
    'отделение структуры': 'Клиника',
    'клиника': 'Клиника',
}


def campaign_period(core, conn, store, campaign, first, last, state=None):
    """Calendar-day spend for one existing campaign, sourced from the monthly ledger.

    An actual campaign-linked payment always wins over the legacy source-level
    named counterpart within that month; never count both representations.
    Missing campaign months remain unknown, not zero. Caller must not combine
    child costs with its source budget a second time.
    """
    source = str(campaign.get('source_key') or '')
    if not source or source == 'yandex_ads':
        return None
    state = state if state is not None else load(conn)
    # This path serves ONLY actual new month-bound payment entries.
    # Historical store.periods.campaigns are exact date-range expenses and
    # stay governed by the existing _cost_entity_summary() contract. Do NOT
    # reinterpret an old 70 RUB on one day as 70 RUB for an entire month.
    all_rows = list(state['entries'].values())
    keys = set(campaign.get('cost_record_keys') or ())
    keys.add(campaign['campaign_key'])
    name = str(campaign.get('campaign_name') or '').strip()
    total = Decimal(0)
    cursor = first
    while cursor <= last:
        month = cursor.strftime('%Y-%m')
        end = min(bounds(month)[1], last)
        subset = [r for r in selected_records(all_rows, month) if r['source'] == source]
        linked = [
            r for r in subset
            if r.get('level') == 'campaign' and r.get('key') in keys
        ]
        if not linked and source == 'instagram' and name in ('Зубачев', 'Клиника'):
            linked = [
                r for r in subset
                if r.get('level') == 'source'
                and LEGACY_INSTAGRAM_PAYMENT_NAMES.get(
                    str(r.get('label') or '').strip().casefold()
                ) == name
            ]
        if not linked or any(r.get('month') is None for r in linked):
            return None
        monthly_amount = sum((Decimal(str(r['amount'])) for r in linked), Decimal(0))
        total += apportioned(monthly_amount, cursor, end)
        cursor = end + timedelta(days=1)
    return float(total)


def direct(core, conn, first, last):
    values = list(core._direct_source_values(conn, first, last).values())
    known = core._nullable_sum(values, 'spend')
    return known, bool(core._dataset_period_complete(conn, 'direct', first, last) and known is not None)


def channel_rows(core, conn, store, rows, first, through):
    """Original discovered source/campaign hierarchy, including unfilled rows.

    Direct amounts are observed API facts through the selected cutoff, never
    monthly manual entries. Completeness is returned separately by view().
    """
    sources = core._source_registry(conn, store)
    campaigns = core._campaign_registry(conn, store)
    source_direct = core._direct_source_values(conn, first, through) if through >= first else {}
    campaign_direct = core._direct_campaign_values(conn, first, through) if through >= first else {}
    result = []
    for meta in sources:
        key = meta['source_key']
        source_rows = [r for r in rows if r['source'] == key]
        own = [r for r in source_rows if r['level'] == 'source']
        def values(entries):
            return {'record_ids': [r['id'] for r in entries],
                    'amount': round(sum(r['amount'] for r in entries), 2) if entries else None}
        children = []
        for campaign in campaigns:
            if campaign['source_key'] != key:
                continue
            keys = set(campaign.get('cost_record_keys') or ()) | {campaign['campaign_key']}
            entries = [r for r in source_rows if r.get('key') in keys and r['level'] == 'campaign']
            children.append({**campaign, **values(entries),
                             'direct': (campaign_direct.get(campaign['campaign_key']) or {}).get('spend')})
        result.append({**meta, **values(own), 'campaigns': children,
                       'fixed_total': fixed_total(source_rows, store) if source_rows else None,
                       'direct': (source_direct.get(key) or {}).get('spend')})
    return result


def view(core, conn, month):
    first, last = bounds(month)
    state, store = load(conn), core._load_cost_store(conn)
    rows = selected_records(records(core, store, state), month)
    fixed = fixed_total(rows, store)
    through = min(last, core._today())
    known, complete = direct(core, conn, first, through) if through >= first else (None, False)
    confirmed = valid_confirmation(core, store, state, month)
    # The obsolete approval remains stored read-only for review; preserve
    # manual adjustments and show why the current month needs new approval.
    stale = state['confirmed'].get(month) if confirmed is None else None
    sources = [{'key': r['source_key'], 'name': r['source_name']} for r in core._source_registry(conn, store)]
    if not any(r['key'] == 'yandex_ads' for r in sources):
        sources.append({'key': 'yandex_ads', 'name': 'Подрядчик Яндекс-рекламы'})
    return {'month': month, 'records': rows, 'sources': sources,
        'channels': channel_rows(core, conn, store, rows, first, through),
        'fixed': fixed, 'direct': known, 'direct_complete': complete,
        'direct_through': through.isoformat() if through >= first else None,
        'automatic_total': round(fixed + known, 2) if fixed is not None and known is not None else None,
        'confirmed': confirmed,
        'stale_confirmation': (
            {'fixed': stale.get('fixed'), 'adjustment': stale.get('adjustment')}
            if stale else None
        ),
        'confirmed_total': round(confirmed['fixed'] + known, 2) if confirmed and known is not None else None,
        'revision': digest([state, store, month, known, complete, through.isoformat()]),
        'history': [r for r in state['history'] if month in r['months']][::-1]}


def apportioned(total, first, last):
    days = calendar.monthrange(first.year, first.month)[1]
    def cumulative(day):
        return (Decimal(str(total)) * day / days).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return cumulative(last.day) - cumulative(first.day - 1)


def period_costs(core, conn, first, last):
    state = load(conn)
    store = core._load_cost_store(conn)
    fixed = Decimal(0)
    cursor = first
    while cursor <= last:
        month = cursor.strftime('%Y-%m')
        _, end = bounds(month)
        end = min(end, last)
        confirmed = valid_confirmation(core, store, state, month)
        if not confirmed:
            fixed = None
            break
        fixed += apportioned(confirmed['fixed'], cursor, end)
        cursor = end + timedelta(days=1)
    known, complete = direct(core, conn, first, last)
    return {'fixed': float(fixed) if fixed is not None else None,
            'direct': known, 'complete': fixed is not None and complete,
            'total': round(float(fixed) + known, 2) if fixed is not None and complete else None}


def affected_months(row):
    if row.get('month'):
        return [row['month']]
    first, last = date.fromisoformat(row['from']), date.fromisoformat(row['to'])
    result = []
    while first <= last:
        result.append(first.strftime('%Y-%m'))
        first = bounds(result[-1])[1] + timedelta(days=1)
    return result


def mutate(core, conn, form, actor, now):
    month = str(form.get('month', ''))
    bounds(month)
    conn.execute('BEGIN IMMEDIATE')
    try:
        current = view(core, conn, month)
        if form.get('revision') != current['revision']:
            raise ValueError('Данные изменились. Обновите страницу перед сохранением.')
        state, store = load(conn), core._load_cost_store(conn)
        action = form.get('action')
        before, after = None, None
        months = {month}
        invalidated = {}
        if action == 'confirm':
            if current['fixed'] is None:
                raise ValueError('Сначала назначьте месяц старым многомесячным платежам.')
            if current['direct'] is None:
                raise ValueError('Нет загруженной суммы Директа. Сначала обновите данные.')
            fixed = current['fixed'] if form.get('mode') == 'auto' else round(amount(form.get('total')) - current['direct'], 2)
            if fixed < 0:
                raise ValueError('Общий итог не может быть меньше расхода Директа.')
            before = state['confirmed'].get(month)
            after = {'fixed': fixed, 'adjustment': round(fixed - current['fixed'], 2),
                     'direct_at_confirmation': current['direct'], 'at': now, 'actor': actor,
                     'records_digest': digest(current['records']),
                     'calculation_version': CONFIRMATION_CALC_VERSION,
                     'base_fixed': current['fixed'],
                     'mode': 'auto' if form.get('mode') == 'auto' else 'manual'}
            state['confirmed'][month] = after
        elif action in ('save', 'delete'):
            entry_id = str(form.get('id') or '')
            if entry_id:
                before = next((r for r in records(core, store, state) if r['id'] == entry_id), None)
                if not before:
                    raise ValueError('Запись уже изменена или удалена.')
                months.update(affected_months(before))
                if before.get('legacy'):
                    bucket = 'sources' if before['level'] == 'source' else 'campaigns'
                    values = store['periods'][bucket][before['key']]
                    store['periods'][bucket][before['key']] = [r for r in values if 'legacy:' + digest([before['level'], before['key'], r]) != entry_id]
                    core._persist_cost_store(conn, store)
                else:
                    del state['entries'][entry_id]
            elif action == 'delete':
                raise ValueError('Выберите запись для удаления.')
            if action == 'delete' and form.get('confirmation') != 'delete':
                raise ValueError('Подтвердите удаление.')
            if action == 'save':
                target_month = str(form.get('target_month', month)); bounds(target_month)
                source = str(form.get('source') or '').strip()
                label = str(form.get('label') or '').strip()
                comment = str(form.get('comment') or '').strip()
                if not label or len(label) > 240 or len(comment) > 2000:
                    raise ValueError('Укажите название платежа (до 240 символов).')
                allowed = {r['key'] for r in current['sources']}
                if source not in allowed and source != 'other':
                    raise ValueError('Выберите источник.')
                # Campaign detail keeps its server-owned identity through every edit.
                # Reclassifying it as a source payment changes deduplication semantics.
                level = before['level'] if before else str(form.get('level') or 'source')
                campaign_key = (before or {}).get('key')
                if level not in ('source', 'campaign'):
                    raise ValueError('Неизвестный уровень расходов.')
                if level == 'campaign' and before is None:
                    campaign_key = str(form.get('campaign_key') or '')
                    campaign = next((r for r in core._campaign_registry(conn, store)
                                     if r['campaign_key'] == campaign_key and r['source_key'] == source), None)
                    if campaign is None or source == 'yandex_ads':
                        raise ValueError('Выберите существующую кампанию; расходы Директа вводятся автоматически.')
                if level == 'campaign' and before and source != before['source']:
                    raise ValueError('Источник детализации кампании изменить нельзя.')
                if source == 'other':
                    source = core._manual_source_key(label)
                    store['sources'][source] = {'name': label, 'channel_name': 'Платная реклама', 'manual': True}
                    core._persist_cost_store(conn, store)
                after = {'id': entry_id if entry_id and not before.get('legacy') else uuid.uuid4().hex,
                         'month': target_month, 'source': source, 'label': label,
                         'amount': amount(form.get('amount')), 'comment': comment,
                         'level': level, 'legacy': False}
                if level == 'campaign':
                    after['key'] = campaign_key
                state['entries'][after['id']] = after
                months.add(target_month)
            for changed_month in months:
                old = state['confirmed'].pop(changed_month, None)
                if old is not None:
                    invalidated[changed_month] = old
        elif action == 'unconfirm':
            before = state['confirmed'].pop(month, None)
            if before is None:
                raise ValueError('Месяц ещё не подтверждён.')
        else:
            raise ValueError('Неизвестное действие.')
        event = {'action': action, 'before': before, 'after': after, 'actor': actor, 'at': now, 'months': sorted(months), 'invalidated_confirmations': invalidated}
        state['history'].append(event)
        conn.execute('INSERT INTO report_blobs(key,payload,updated_by,updated_at) VALUES(?,?,?,?) '
                     'ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,updated_by=excluded.updated_by,updated_at=excluded.updated_at',
                     (KEY, encode(state), actor, now))
        conn.execute('INSERT INTO audit_log(actor_user_id,action,target_user_id,details,created_at) VALUES(?,?,?,?,?)',
                     (actor, 'advertising_monthly_' + action, actor, encode(event), now))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def register(app, core):
    @app.get('/api/reports/advertising/costs/monthly')
    @core.permission_required('upload_services')
    def advertising_monthly_get():
        try:
            return core.jsonify(**view(core, core.db(), core.request.args.get('month') or core._today().strftime('%Y-%m')))
        except ValueError as exc:
            return core.jsonify(status='error', message=str(exc)), 400

    @app.post('/api/reports/advertising/costs/monthly')
    @core.permission_required('upload_services')
    def advertising_monthly_post():
        core.require_csrf()
        try:
            mutate(core, core.db(), core.request.form, core.g.user['id'], core.iso_now())
            return core.jsonify(status='ok', message='Сохранено.')
        except ValueError as exc:
            return core.jsonify(status='error', message=str(exc)), 409
