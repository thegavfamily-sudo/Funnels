# ============================================================================
# functions.py — системные сообщения, executeFunction, логические формулы
#
# Порты из apps_script/БОТ_готовый.gs:
#   Utils.getSystemMessage              :817
#   getFunctionConfigFromSheet          :5184
#   executeFunctionFromSheet            :5246
#   executeLogicalFormula               :5080
#   executeSingleFormula                :4959
#   parseFormulaParams / checkCondition :4814/:4894
#   getCellValueByConditions            :4846
#   getAllValuesByConditions            :4868
#   getUserFieldFromAuth                :5146
#
# Внешние таблицы (condition вида `tableId!SheetName`) читаются из Supabase
# по имени таблицы = часть ДО '!' (если '!' нет — вся строка), PostgREST GET.
# ============================================================================

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

import users
from storage import BotContext, normalize_column_key
from supabase_client import SupabaseError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Системные сообщения (Funnel info message / Funnel final message)
# ---------------------------------------------------------------------------

def get_system_message(ctx: BotContext, message_type: str) -> str:
    """Порт Utils.getSystemMessage (:817): кэш `${funnel_id}_${type}`."""
    if not ctx.funnel_id or not message_type:
        return ''
    if not ctx.config.get('systemmessages', True):
        return ''
    return ctx.storage.get_system_message(ctx.funnel_id, message_type)


def get_system_info_message(ctx: BotContext) -> str:
    return get_system_message(ctx, 'Funnel info message')


def get_system_final_message(ctx: BotContext) -> str:
    return get_system_message(ctx, 'Funnel final message')


# ---------------------------------------------------------------------------
# Поиск конфига функции
# ---------------------------------------------------------------------------

def get_function_config(ctx: BotContext, function_name: str) -> Optional[dict]:
    """Порт getFunctionConfigFromSheet (:5184) по Function name."""
    for entry in ctx.storage.functions_rows:
        data = entry['data'] or {}
        row_name = str(data.get('function_name') or '').strip()
        if row_name != function_name:
            continue
        row_funnel_id = entry['funnel_id']
        if row_funnel_id and row_funnel_id != ctx.funnel_id:
            continue
        return {
            'type': str(data.get('type') or '').strip(),
            'name': row_name,
            'message': str(data.get('message') or '').strip(),
            'condition': str(data.get('condition') or '').strip(),
            'function_param': str(data.get('function') or '').strip(),
        }
    return None


# ---------------------------------------------------------------------------
# Внешние таблицы Supabase
# ---------------------------------------------------------------------------

def _table_name_from_condition(condition_part: str) -> str:
    """`tableId!SheetName` → tableId; без '!' — вся строка (спека порта)."""
    return condition_part.split('!', 1)[0].strip()


def _fetch_table(ctx: BotContext, table_name: str) -> tuple[Optional[list], Optional[str]]:
    """→ (rows, error_message). rows=None при ошибке."""
    try:
        rows = ctx.storage.client.select(table_name)
        return rows, None
    except SupabaseError as error:
        text = str(error)
        if '404' in text or 'PGRST205' in text or 'does not exist' in text:
            return None, 'not_found'
        return None, f'❌ Ошибка: {error}'
    except Exception as error:  # noqa: BLE001
        return None, f'❌ Ошибка: {error}'


def _table_headers(rows: list[dict]) -> list[str]:
    headers: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in headers:
                headers.append(key)
    return headers


# ---------------------------------------------------------------------------
# getUserFieldFromAuth (:5146)
# ---------------------------------------------------------------------------

def get_user_field_from_auth(ctx: BotContext, user_id: Any,
                             field_name: str) -> Any:
    try:
        # Одна таблица Supabase (строка «Supabase table»); строка auth —
        # фолбэк для старых воронок.
        auth_config = (get_function_config(ctx, 'Supabase table')
                       or get_function_config(ctx, 'auth'))
        if not auth_config:
            return None

        condition = auth_config['condition']
        parts = condition.split('!')
        table_id = parts[0].strip()
        # sheet_name = parts[1] if len(parts) > 1 else 'Users' — имя Supabase
        # таблицы = часть ДО '!' (спека), лист-суффикс не используется.

        rows, error = _fetch_table(ctx, table_id)
        if rows is None:
            return None

        headers = _table_headers(rows)
        chat_id_col = None
        field_col = None
        target_field = field_name.lower()

        for header in headers:
            header_str = str(header).strip()
            if header_str == 'Chat id' or normalize_column_key(header) == 'chat_id':
                chat_id_col = header
            if header_str.lower() == target_field \
                    or normalize_column_key(header) == normalize_column_key(field_name):
                field_col = header

        if chat_id_col is None or field_col is None:
            return None

        for row in rows:
            if str(row.get(chat_id_col) or '').strip() == str(user_id):
                return row.get(field_col)
        return None
    except Exception as error:  # noqa: BLE001
        logger.error('[getUserFieldFromAuth] %s', error)
        return None


# ---------------------------------------------------------------------------
# Формулы: парсинг параметров и условия
# ---------------------------------------------------------------------------

def parse_formula_params(params_str: str) -> list[str]:
    """Порт parseFormulaParams (:4814): запятые на верхнем уровне, кавычки/скобки."""
    result: list[str] = []
    current = ''
    depth = 0
    in_quotes = False

    for i, char in enumerate(params_str):
        if char == '"' and (i == 0 or params_str[i - 1] != '\\'):
            in_quotes = not in_quotes
            current += char
        elif char == '(' and not in_quotes:
            depth += 1
            current += char
        elif char == ')' and not in_quotes:
            depth -= 1
            current += char
        elif char == ',' and depth == 0 and not in_quotes:
            result.append(current.strip())
            current = ''
        else:
            current += char
    if current.strip() != '':
        result.append(current.strip())
    return result


def _to_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            pass
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d.%m.%Y %H:%M:%S'):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def _to_midnight(value: Any) -> Optional[datetime]:
    dt = _to_datetime(value)
    if dt is None:
        return None
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _loose_eq(a: Any, b: Any) -> bool:
    """JS-подобное == (числовое сравнение при возможности)."""
    if isinstance(a, datetime) or isinstance(b, datetime) \
            or isinstance(a, date) or isinstance(b, date):
        da, db = _to_datetime(a), _to_datetime(b)
        if da is not None and db is not None:
            return da == db
        return False
    if a is None or b is None:
        return a is None and b is None
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        pass
    return str(a) == str(b)


def _compare(operator: str, a: Any, b: Any) -> bool:
    try:
        if operator == '=':
            return _loose_eq(a, b)
        if operator == '!=':
            return not _loose_eq(a, b)
        # Реляционные операторы: сначала даты, затем числа, затем строки
        if isinstance(a, (datetime, date)) or isinstance(b, (datetime, date)):
            da, db = _to_datetime(a), _to_datetime(b)
            if da is None or db is None:
                return False
            a, b = da, db
        else:
            try:
                a, b = float(a), float(b)
            except (TypeError, ValueError):
                a, b = str(a), str(b)
        if operator == '<':
            return a < b
        if operator == '>':
            return a > b
        if operator == '<=':
            return a <= b
        if operator == '>=':
            return a >= b
        return True
    except Exception:  # noqa: BLE001
        return False


def check_condition(condition: str, row: dict, headers: list[str],
                    today: datetime, user_id: Any) -> bool:
    """Порт checkCondition (:4894): операторы <= >= < > = !=,
    today(), today()-N, {user_id}, "строка", числа."""
    operators = ['<=', '>=', '<', '>', '=', '!=']
    operator = None
    field = ''
    value = ''

    for op in operators:
        if op in condition:
            operator = op
            parts = condition.split(op, 1)
            field = parts[0].strip()
            value = parts[1].strip() if len(parts) > 1 else ''
            break

    if not operator:
        return True

    if field not in headers:
        return False

    row_value = row.get(field)
    compare_value: Any = value

    if value == 'today()':
        compare_value = today
        converted = _to_midnight(row_value)
        if converted is not None:
            row_value = converted
    elif 'today()-' in value:
        match = re.search(r'today\(\)-(\d+)', value)
        days = int(match.group(1)) if match else 0
        compare_value = today - timedelta(days=days)
        converted = _to_midnight(row_value)
        if converted is not None:
            row_value = converted
    elif value == '{user_id}':
        compare_value = str(user_id)
        row_value = '' if row_value is None else str(row_value)
    elif len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        compare_value = value[1:-1]
        row_value = '' if row_value is None else str(row_value)
    else:
        try:
            float(value)
            value_is_number = True
        except ValueError:
            value_is_number = False
        if value_is_number and isinstance(row_value, (int, float)) \
                and not isinstance(row_value, bool):
            compare_value = float(value)

    return _compare(operator, row_value, compare_value)


def _rows_matching(rows: list[dict], headers: list[str], conditions: list[str],
                   today: datetime, user_id: Any) -> list[dict]:
    matched = []
    for row in rows:
        if all(check_condition(cond, row, headers, today, user_id)
               for cond in conditions):
            matched.append(row)
    return matched


def get_cell_value_by_conditions(rows: list[dict], headers: list[str],
                                 value_column: int, conditions: list[str],
                                 user_id: Any) -> Any:
    """Порт :4846 — значение первой подходящей строки (value_column = ключ)."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for row in _rows_matching(rows, headers, conditions, today, user_id):
        return row.get(value_column)
    return None


def get_all_values_by_conditions(rows: list[dict], headers: list[str],
                                 value_column: int, conditions: list[str],
                                 user_id: Any) -> list[str]:
    """Порт :4868 — все непустые значения подходящих строк."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    values: list[str] = []
    for row in _rows_matching(rows, headers, conditions, today, user_id):
        value = row.get(value_column)
        if value is not None and value != '':
            values.append(str(value))
    return values


# ---------------------------------------------------------------------------
# executeSingleFormula (:4959)
# ---------------------------------------------------------------------------

def execute_single_formula(formula: str, rows: list[dict], headers: list[str],
                           user_id: Any) -> Any:
    match = re.match(r'^(\w+)\((.*)\)$', formula, re.DOTALL)
    if not match:
        return '❌ Неверный формат формулы'

    action = match.group(1)
    params = parse_formula_params(match.group(2))
    if not params:
        return '❌ Не указаны параметры'

    value_column = params[0]
    conditions = params[1:]

    if value_column not in headers:
        return f'❌ Колонка "{value_column}" не найдена'

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if action == 'sum':
        total = 0.0
        for row in _rows_matching(rows, headers, conditions, today, user_id):
            try:
                total += float(row.get(value_column))
            except (TypeError, ValueError):
                continue
        return total if total != int(total) else int(total)

    if action == 'mul':
        product: Optional[float] = None
        for row in _rows_matching(rows, headers, conditions, today, user_id):
            try:
                value = float(row.get(value_column))
            except (TypeError, ValueError):
                continue
            product = value if product is None else product * value
        if product is None:
            return 0
        return int(product) if product == int(product) else product

    if action == 'avg':
        total = 0.0
        count = 0
        for row in _rows_matching(rows, headers, conditions, today, user_id):
            try:
                total += float(row.get(value_column))
                count += 1
            except (TypeError, ValueError):
                continue
        if count > 0:
            return f'{total / count:.2f}'
        return 0

    if action == 'count':
        return len(_rows_matching(rows, headers, conditions, today, user_id))

    if action == 'find':
        result = get_cell_value_by_conditions(rows, headers, value_column,
                                              conditions, user_id)
        return result if result is not None else '—'

    if action == 'text':
        result = get_cell_value_by_conditions(rows, headers, value_column,
                                              conditions, user_id)
        return str(result) if result is not None else '—'

    if action == 'findall':
        values = get_all_values_by_conditions(rows, headers, value_column,
                                              conditions, user_id)
        return ', '.join(values) if values else '—'

    if action == 'findlist':
        values = get_all_values_by_conditions(rows, headers, value_column,
                                              conditions, user_id)
        return '\n'.join(values) if values else '—'

    return f'❌ Неизвестное действие: {action}'


# ---------------------------------------------------------------------------
# calc — арифметика над тегами пользователя (coins = coins * dice)
# ---------------------------------------------------------------------------

class CalcError(Exception):
    """Ошибка формулы calc; текст показывается как ответ бота."""


def _calc_tokenize(expr: str) -> list[str]:
    """Токены: числа, идентификаторы (теги/эмодзи), операторы, скобки."""
    tokens: list[str] = []
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit() or (ch == '.' and i + 1 < n and expr[i + 1].isdigit()):
            j = i
            while j < n and (expr[j].isdigit() or expr[j] == '.'):
                j += 1
            tokens.append(expr[i:j])
            i = j
            continue
        if ch in '+-*/(),=':
            tokens.append(ch)
            i += 1
            continue
        j = i
        while (j < n and not expr[j].isspace()
               and expr[j] not in '+-*/(),='):
            j += 1
        tokens.append(expr[i:j])
        i = j
    return tokens


class _CalcParser:
    """Рекурсивный спуск: expr := term ((+|-) term)* … Без eval."""

    def __init__(self, tokens: list[str], tags: dict):
        self.tokens = tokens
        self.pos = 0
        self.tags = tags

    def peek(self) -> Optional[str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> Optional[str]:
        tok = self.peek()
        self.pos += 1
        return tok

    def expect(self, tok: str) -> None:
        got = self.next()
        if got != tok:
            raise CalcError(f'ожидалось «{tok}», получено «{got or ""}»')

    def parse(self) -> float:
        if not self.tokens:
            raise CalcError('пустое выражение')
        value = self.expr()
        if self.pos < len(self.tokens):
            raise CalcError(
                f'лишние символы: {" ".join(self.tokens[self.pos:])}')
        return value

    def expr(self) -> float:
        value = self.term()
        while self.peek() in ('+', '-'):
            op = self.next()
            rhs = self.term()
            value = value + rhs if op == '+' else value - rhs
        return value

    def term(self) -> float:
        value = self.unary()
        while self.peek() in ('*', '/'):
            op = self.next()
            rhs = self.unary()
            if op == '*':
                value = value * rhs
            else:
                if rhs == 0:
                    raise CalcError('деление на ноль')
                value = value / rhs
        return value

    def unary(self) -> float:
        if self.peek() == '-':
            self.next()
            return -self.unary()
        if self.peek() == '+':
            self.next()
            return self.unary()
        return self.primary()

    def primary(self) -> float:
        tok = self.next()
        if tok is None:
            raise CalcError('выражение оборвано')
        if tok == '(':
            value = self.expr()
            self.expect(')')
            return value
        if tok in (')', ',', '+', '-', '*', '/', '='):
            raise CalcError(f'неожиданный символ «{tok}»')
        if tok in ('min', 'max'):
            self.expect('(')
            args = [self.expr()]
            while self.peek() == ',':
                self.next()
                args.append(self.expr())
            self.expect(')')
            return min(args) if tok == 'min' else max(args)
        try:
            return float(tok)
        except ValueError:
            pass
        if tok in self.tags:
            return self._to_number(tok, self.tags[tok])
        # Нет тега — стартовое значение (счётчик начинается с нуля).
        return 0.0

    @staticmethod
    def _to_number(name: str, value: Any) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        try:
            return float(str(value).strip().replace(',', '.'))
        except (TypeError, ValueError):
            raise CalcError(f'тег «{name}» не число: «{value}»')


def evaluate_calc(expr: str, tags: dict) -> float:
    """Вычислить выражение над тегами (без записи). Для тестов/внутреннего."""
    return _CalcParser(_calc_tokenize(expr), tags).parse()


def _calc_is_ident(name: str) -> bool:
    return bool(name) and not any(
        c.isspace() or c in '+-*/(),=' for c in name)


def _calc_normalize(value: float) -> Any:
    if abs(value - round(value)) < 1e-9:
        return int(round(value))
    return round(value, 6)


def execute_calc_formula(ctx: BotContext, user_id: Any, formula: str,
                         message: str) -> dict:
    """Формула вида `coins = coins * dice`: считает, пишет тег, отвечает.

    {result} в сообщении → результат; остальные {теги} подставляются
    после записи, т.е. показывают уже новое значение.
    """
    try:
        if not user_id:
            return {'success': False,
                    'message': '❌ Расчёт доступен только для пользователя'}
        formula = str(formula or '').strip()
        if not formula:
            return {'success': False, 'message': '❌ Не указана формула'}
        if '=' not in formula:
            return {'success': False,
                    'message': '❌ Формула должна быть вида: coins = coins * dice'}
        target, expr = formula.split('=', 1)
        target = target.strip()
        if not _calc_is_ident(target):
            return {'success': False,
                    'message': f'❌ Нельзя записать результат в «{target}»'}

        row = ctx.storage.get_user(ctx.funnel_id, user_id)
        data: dict = dict(row.get('data') or {}) if row else {}
        value = _calc_normalize(evaluate_calc(expr, data))
        data[target] = value
        ctx.storage.save_user_row(ctx.funnel_id, user_id,
                                  users.get_user_state(ctx, user_id), data)

        text = message or '✅ Результат: {result}'
        text = text.replace('{result}', str(value))
        return {'success': True,
                'message': users.replace_tags_in_text(ctx, text, user_id)}
    except CalcError as error:
        return {'success': False, 'message': f'❌ {error}'}
    except Exception as error:  # noqa: BLE001
        logger.error('[executeCalcFormula] Error: %s', error)
        return {'success': False, 'message': f'❌ Ошибка расчёта: {error}'}


# ---------------------------------------------------------------------------
# executeLogicalFormula (:5080)
# ---------------------------------------------------------------------------

def execute_logical_formula(ctx: BotContext, user_id: Any, condition: str,
                            formula: str) -> Any:
    try:
        if not formula:
            return '❌ Ошибка: не указан Function'
        if not condition:
            # Condition не нужен: одна таблица Supabase — строка
            # «Supabase table»; auth — фолбэк для старых воронок.
            table_cfg = (get_function_config(ctx, 'Supabase table')
                         or get_function_config(ctx, 'auth') or {})
            table = str(table_cfg.get('condition') or 'Users').split('|')[0]
            condition = table.strip() or 'Users'

        condition_parts = condition.split('|')
        table_info = condition_parts[0].split('!')
        table_name = table_info[0].strip()
        sheet_name = table_info[1].strip() if len(table_info) > 1 else ''
        actions = condition_parts[1:]

        extra_params: dict[str, Any] = {}
        for action in actions:
            if action == 'auth':
                field_to_get = next(
                    (a for a in actions if a in ('mail', 'phone', 'name', 'id')),
                    None)
                if field_to_get:
                    field_value = get_user_field_from_auth(
                        ctx, user_id, field_to_get)
                    if field_value:
                        extra_params[field_to_get] = field_value
            elif action in ('mail', 'phone', 'name', 'id'):
                field_value = get_user_field_from_auth(ctx, user_id, action)
                if field_value:
                    extra_params[action] = field_value

        rows, error = _fetch_table(ctx, table_name)
        if rows is None:
            if error == 'not_found':
                return f'❌ Лист "{sheet_name}" не найден'
            return error or '❌ Ошибка: неизвестная ошибка'

        headers = _table_headers(rows)

        processed_formula = str(formula)
        processed_formula = processed_formula.replace('{user_id}', str(user_id))
        for key, value in extra_params.items():
            processed_formula = re.sub(
                r'\{' + re.escape(str(key)) + r'\}',
                lambda _m, v=str(value): v, processed_formula)

        if '|' in processed_formula:
            formulas = [f.strip() for f in processed_formula.split('|')]
            return [execute_single_formula(f, rows, headers, user_id)
                    for f in formulas]
        return execute_single_formula(processed_formula, rows, headers, user_id)

    except Exception as error:  # noqa: BLE001
        return f'❌ Ошибка: {error}'


# ---------------------------------------------------------------------------
# executeFunctionFromSheet (:5246)
# ---------------------------------------------------------------------------

def execute_function(ctx: BotContext, function_name: str, user_id: Any,
                     chat_id: Any) -> dict:
    func_config = get_function_config(ctx, function_name)

    if not func_config:
        return {'success': False,
                'message': f'❌ Функция "{function_name}" не найдена'}

    func_type = func_config['type']

    if func_type in ('Funnel system', 'System', 'Custom'):
        message = func_config['message']
        if user_id:
            message = users.replace_tags_in_text(ctx, message, user_id)
        return {'success': True, 'message': message}

    if func_type == 'builtin':
        if function_name == 'getMyName':
            info = users.get_user_info(ctx, user_id)
            name = ''
            if info:
                name = info.get('first_name') or info.get('username') or ''
            if not name:
                name = user_id
            message = func_config['message'] or '👤 {result}'
            message = message.replace('{result}', str(name), 1)
            return {'success': True, 'message': message}
        if function_name == 'getTime':
            time_str = datetime.now().strftime('%d.%m.%Y, %H:%M:%S')
            message = func_config['message'] or '🕐 {result}'
            message = message.replace('{result}', time_str, 1)
            return {'success': True, 'message': message}
        if function_name == 'getUserId':
            message = func_config['message'] or '🆔 {result}'
            message = message.replace('{result}', str(user_id), 1)
            return {'success': True, 'message': message}
        return {'success': False,
                'message': f'❌ Неизвестная builtin функция: {function_name}'}

    if func_type == 'logical':
        result = execute_logical_formula(
            ctx, user_id, func_config['condition'], func_config['function_param'])
        message = func_config['message']

        if isinstance(result, list):
            for i, item in enumerate(result):
                message = re.sub(r'\{' + str(i + 1) + r'\}',
                                 lambda _m, v=str(item): v, message)
        else:
            message = message.replace('{result}', str(result), 1)

        if user_id:
            message = users.replace_tags_in_text(ctx, message, user_id)
        return {'success': True, 'message': message}

    if func_type == 'calc':
        return execute_calc_formula(ctx, user_id,
                                    func_config['function_param'],
                                    func_config['message'])

    return {'success': False,
            'message': f'❌ Неизвестный тип функции: {func_type}'}
