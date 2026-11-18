# ============================================================================
# function_seed.py — системные функции по умолчанию (воронка 016eb293).
#
# Зачем: бот и приложение должны видеть эти функции даже без строк
# в Supabase/SQLite (сид при refresh / при первом открытии БД).
#
# Формат — как у бота: entry {id, funnel_id, data}, data — type,
# function_name, message, condition, function.
# Condition без ID Google-таблиц: имя таблицы Supabase (бот делает select).
# ============================================================================

from __future__ import annotations

from typing import Any

# Канонические имена системных сообщений (config.SYSTEM_MESSAGE_*).
SYSTEM_FUNNEL_ID = '016eb293'

FUNCTION_SEED: list[dict[str, Any]] = [
    {
        'id': 'fn_info001',
        'funnel_id': SYSTEM_FUNNEL_ID,
        'data': {
            'type': 'System',
            'function_name': 'Funnel info message',
            'message': (
                'ffddj  jjjjrt tyjtyjrtj rtryjtyjtj tyjtyjtjtj rtyjtyjtrj '
                'tyjtyjt  tjtrjtrjtyyj tyjtyjtj tyjtj ty'
            ),
            'condition': '',
            'function': '',
        },
    },
    {
        'id': 'fn_final01',
        'funnel_id': SYSTEM_FUNNEL_ID,
        'data': {
            'type': 'System',
            'function_name': 'Funnel final message',
            'message': 'Это все',
            'condition': '',
            'function': '',
        },
    },
    {
        'id': 'fn_table01',
        'funnel_id': SYSTEM_FUNNEL_ID,
        'data': {
            'type': 'System',
            'function_name': 'Supabase table',
            'message': '',
            'condition': 'Users',
            'function': '',
        },
    },
    {
        'id': 'fn_live001',
        'funnel_id': SYSTEM_FUNNEL_ID,
        'data': {
            'type': 'Custom',
            'function_name': 'Live message',
            'message': (
                'thrtherthrt erherhrethrth erherherh rthrehreh '
                'rthtrhh rthrthr'
            ),
            'condition': '',
            'function': '',
        },
    },
    {
        'id': 'fn_act0001',
        'funnel_id': SYSTEM_FUNNEL_ID,
        'data': {
            'type': 'logical',
            'function_name': 'activitysum',
            'message': 'Вот Ваши данные {result}',
            'condition': 'Activity|auth|mail',
            'function': 'sum(Caloric content, Choice="Шаги")',
        },
    },
]


def merge_function_seeds(existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Добавляет в existing сиды, которых ещё нет (по id и по name+funnel)."""
    out = list(existing)
    seen_ids = {str(e.get('id') or '') for e in out}
    seen_keys = {
        (
            str(e.get('funnel_id') or '').strip(),
            str((e.get('data') or {}).get('function_name') or '').strip(),
        )
        for e in out
    }
    for seed in FUNCTION_SEED:
        data = seed['data']
        key = (seed['funnel_id'], data['function_name'])
        if seed['id'] in seen_ids or key in seen_keys:
            continue
        out.append({
            'id': seed['id'],
            'funnel_id': seed['funnel_id'],
            'data': dict(data),
        })
        seen_ids.add(seed['id'])
        seen_keys.add(key)
    return out
