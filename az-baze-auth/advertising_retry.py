"""Durable retry scheduling; enabled only by an installed state file.

Both manual and timer calls hold this gate until outcome is persisted. The
existing Metrika lock and transactional importer remain authoritative.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import fcntl
import functools
import json
import os
from pathlib import Path
import re
import tempfile
import time
import metrika_auto
import direct_auto

STATE = Path('/var/lib/az-baze/advertising-retry.json')
DELAY = 600

class RetrySkipped(Exception):
    pass


def write_state(value):
    fd, name = tempfile.mkstemp(prefix='.advertising-retry-', dir=STATE.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f); f.flush(); os.fsync(f.fileno())
        os.replace(name, STATE)
    finally:
        if os.path.exists(name): os.unlink(name)


def permanent(exc):
    return isinstance(exc, (metrika_auto.MetrikaAuthError, metrika_auto.MetrikaAccessError)) or (
        isinstance(exc, direct_auto.DirectApiError) and
        bool(re.search(r'HTTP (401|403)\b|код=(53|54)\b', str(exc))))


def public_status():
    """Read-only, allowlisted UI projection; never expose exception details."""
    def stamp(value):
        return datetime.fromtimestamp(value, ZoneInfo('Asia/Novosibirsk')).strftime('%d.%m.%Y в %H:%M')
    try:
        state = json.loads(STATE.read_text())
        auto = state.get('automatic') or {}
        outcome = auto.get('outcome')
        if outcome == 'success':
            message = 'Автоматическая проверка успешно завершена — ' + stamp(auto['at']) + '.'
        elif outcome == 'error':
            message = 'Автоматическая проверка завершилась ошибкой — ' + stamp(auto['at']) + '.'
        elif outcome == 'started':
            message = 'Автоматическая проверка начата — ' + stamp(auto['at']) + '; завершение пока не подтверждено.'
        else:
            message = 'Результат автоматической проверки пока не зарегистрирован.'
        if state.get('blocked'):
            message += ' Автозагрузка приостановлена из-за ошибки доступа. Требуется успешная ручная загрузка после восстановления доступа.'
        elif state.get('next_retry') is not None:
            due = state['next_retry']
            message += (' Повтор ожидается на ближайшей минутной проверке.' if due <= time.time()
                        else ' Следующая попытка — не ранее ' + stamp(due) + '.')
        return {'message': message + ' Время Новосибирска.'}
    except FileNotFoundError:
        return {'message': 'Статус автоматической проверки пока недоступен.'}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return {'message': 'Не удалось прочитать статус автоматической проверки.'}


def scheduled(fn):
    @functools.wraps(fn)
    def call(conn, token, actor_user_id, *, retry_mode=None):
        if not STATE.exists():
            if retry_mode == 'retry': raise RetrySkipped('disabled')
            return fn(conn, token, actor_user_id)
        with open(str(STATE)+'.lock', 'a') as gate:
            try: fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise metrika_auto.MetrikaBusyError('Другая загрузка уже выполняется.') from None
            state = json.loads(STATE.read_text())
            now = time.time()
            due = state.get('next_retry')
            if retry_mode:
                if state.get('blocked'): raise RetrySkipped('access_error')
                if due is not None and now < due: raise RetrySkipped('waiting')
                if retry_mode == 'retry' and due is None: raise RetrySkipped('no_retry')
                if due is None and state.get('last_success', 0) >= int(now//3600)*3600:
                    raise RetrySkipped('already_updated_this_hour')
            # Persist intent BEFORE network work: timeout/kill must not lose
            # a manual failure. Active work remains protected by the gate.
            was_blocked = bool(state.get('blocked'))
            # Access recovery is proven only by success, never by starting
            # a manual attempt. Keep this durable even if the process dies.
            intent = {**state, 'next_retry': None if was_blocked else now + DELAY,
                      'blocked': was_blocked}
            if retry_mode:
                intent['automatic'] = {'outcome': 'started', 'at': now}
            write_state(intent)
            try:
                result = fn(conn, token, actor_user_id)
            except metrika_auto.MetrikaBusyError:
                write_state(state)
                raise
            except Exception as exc:
                try: conn.rollback()
                except Exception: pass
                blocked = was_blocked or permanent(exc) or not token
                if retry_mode:
                    intent['automatic'] = {'outcome': 'error', 'at': time.time()}
                write_state({**intent, 'next_retry': None if blocked else time.time()+DELAY,
                             'blocked': blocked, 'error_type': type(exc).__name__})
                # Carry only the decision for this request, not a racy reread.
                exc.az_retry_scheduled = not blocked
                raise
            success_at = time.time()
            automatic = ({'outcome': 'success', 'at': success_at} if retry_mode
                         else state.get('automatic'))
            write_state({'last_success': success_at, 'next_retry': None, 'blocked': False,
                         'automatic': automatic})
            return result
    return call
