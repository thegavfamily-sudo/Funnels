# ============================================================================
# functions.py — системные сообщения, executeFunction, расчёт (calc) и
# условие (condition) по тегам пользователя
#
# Порты из apps_script/БОТ_готовый.gs:
#   Utils.getSystemMessage              :817
#   getFunctionConfigFromSheet          :5184
#   executeFunctionFromSheet            :5246
# ============================================================================

from __future__ import annotations

import logging
from typing import Any, Optional

import config
import users
from storage import BotContext

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

    def _int_field(value: Any) -> int:
        try:
            return int(str(value or '').strip() or 0)
        except ValueError:
            return 0

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
            # Условие: шаги ветки (0 — без перехода).
            'step_yes': _int_field(data.get('step_yes')),
            'step_no': _int_field(data.get('step_no')),
            # От кого запускать calc/condition: command|step|dice|poll|quiz.
            # Пусто = command (старые строки).
            'trigger': str(data.get('trigger') or '').strip() or 'command',
            'trigger_step': _int_field(data.get('trigger_step')),
        }
    return None


# ---------------------------------------------------------------------------
# Триггеры: от кого запускать calc/condition
# ---------------------------------------------------------------------------

def run_functions_for_step(ctx: BotContext, user_id: Any, step_number: int,
                           pending_branch: int = 0) -> tuple[list[str], int]:
    """Функции с trigger=step при показе шага. → (сообщения, ветка)."""
    msgs: list[str] = []
    branch = pending_branch or 0
    for result in _run_matching(ctx, user_id, 'step', step_number):
        msgs.extend(result.get('messages') or [])
        st = _int_field(result.get('step'))
        if st > 0:
            branch = st
    return msgs, branch


def run_functions_for_trigger(ctx: BotContext, user_id: Any,
                              kind: str) -> tuple[list[str], int]:
    """Функции с trigger=dice|poll|quiz → (сообщения, ветка condition)."""
    msgs: list[str] = []
    branch = 0
    for result in _run_matching(ctx, user_id, kind, 0):
        msgs.extend(result.get('messages') or [])
        st = _int_field(result.get('step'))
        if st > 0:
            branch = st
    return msgs, branch


def _int_field(value: Any) -> int:
    try:
        return int(str(value or '').strip() or 0)
    except ValueError:
        return 0


def _run_matching(ctx: BotContext, user_id: Any, trigger: str,
                  step_number: int) -> list[dict]:
    out: list[dict] = []
    # storage может быть упрощённым (тесты) — без functions_rows просто пусто.
    rows = getattr(ctx.storage, 'functions_rows', None) or []
    for entry in rows:
        data = entry.get('data') or {}
        name = str(data.get('function_name') or '').strip()
        if not name:
            continue
        row_funnel = entry.get('funnel_id')
        if row_funnel and row_funnel != ctx.funnel_id:
            continue
        fn_trigger = str(data.get('trigger') or '').strip() or 'command'
        if fn_trigger != trigger:
            continue
        if trigger == 'step' and _int_field(data.get('trigger_step')) != step_number:
            continue
        result = execute_function(ctx, name, user_id, 0)
        out.append({
            'messages': [result['message']]
            if str(result.get('message') or '').strip() else [],
            'step': result.get('step') or 0,
        })
    return out


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

        # Сообщение необязательно: бот отвечает только если оно задано,
        # иначе молчит — результат виден в шагах через {имя переменной}.
        if not str(message or '').strip():
            return {'success': True, 'message': ''}
        text = str(message).replace('{result}', str(value))
        return {'success': True,
                'message': users.replace_tags_in_text(ctx, text, user_id)}
    except CalcError as error:
        return {'success': False, 'message': f'❌ {error}'}
    except Exception as error:  # noqa: BLE001
        logger.error('[executeCalcFormula] Error: %s', error)
        return {'success': False, 'message': f'❌ Ошибка расчёта: {error}'}


# ---------------------------------------------------------------------------
# Условие — проверка тега пользователя (coins >= 100)
# Для type='condition': condition — проверка, message — «если выполнилось»,
# function — «если не выполнилось».
# ---------------------------------------------------------------------------

_CONDITION_OPS = ('>=', '<=', '!=', '==', '>', '<', '=')


def _parse_scalar(text: str) -> Any:
    """'100' → число; '"VIP"' → строка VIP; 'vip' → строка vip."""
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    try:
        return float(text)
    except ValueError:
        return text


def parse_condition(condition: str) -> Optional[tuple[str, str, Any]]:
    """'coins >= 100' → (тег, оператор, значение); None при ошибке."""
    text = str(condition or '').strip()
    if not text:
        return None
    for op in _CONDITION_OPS:
        if op in text:
            left, right = text.split(op, 1)
            tag, right = left.strip(), right.strip()
            if not tag or not right:
                return None
            return tag, op, _parse_scalar(right)
    return None


def _cond_compare(raw: Any, op: str, rhs: Any) -> bool:
    if isinstance(rhs, (int, float)) and not isinstance(rhs, bool):
        # Числовое сравнение; нет тега или он пуст — 0 (как в calc).
        if raw is None or str(raw).strip() == '':
            left = 0.0
        else:
            try:
                left = float(str(raw).strip().replace(',', '.'))
            except (TypeError, ValueError):
                return False
        right = float(rhs)
    else:
        left = '' if raw is None else str(raw)
        right = str(rhs)
    if op == '>=':
        return left >= right
    if op == '<=':
        return left <= right
    if op in ('=', '=='):
        return left == right
    if op == '!=':
        return left != right
    if op == '>':
        return left > right
    if op == '<':
        return left < right
    return False


def execute_condition(ctx: BotContext, user_id: Any, condition: str,
                      yes_text: str, no_text: str,
                      step_yes: int = 0, step_no: int = 0) -> dict:
    """Проверка тега → текст ветки + целевой шаг ('step', 0 — без перехода)."""
    try:
        if not user_id:
            return {'success': False,
                    'message': '❌ Проверка доступна только для пользователя'}
        parsed = parse_condition(condition)
        if not parsed:
            return {'success': False,
                    'message': f'❌ Неверная проверка: {condition}'}
        tag, op, rhs = parsed
        row = ctx.storage.get_user(ctx.funnel_id, user_id)
        data: dict = dict(row.get('data') or {}) if row else {}
        ok = _cond_compare(data.get(tag), op, rhs)
        step = step_yes if ok else step_no
        text = yes_text if ok else no_text
        if not str(text or '').strip():
            return {'success': True, 'message': '', 'step': step}
        return {'success': True,
                'message': users.replace_tags_in_text(ctx, text, user_id),
                'step': step}
    except Exception as error:  # noqa: BLE001
        logger.error('[executeCondition] %s', error)
        return {'success': False, 'message': f'❌ Ошибка проверки: {error}'}


# ---------------------------------------------------------------------------
# Purchase — проверка баланса и списание для кнопки типа purchase.
# Проверка и списание в одном вызове: окна для гонки между ними нет.
# ---------------------------------------------------------------------------

def _purchase_number(raw: Any) -> float:
    """Значение тега/цены как число; пусто/мусор → 0."""
    if raw is None or str(raw).strip() == '':
        return 0.0
    try:
        return float(str(raw).strip().replace(',', '.'))
    except (TypeError, ValueError):
        return 0.0


def _purchase_normalize(value: float) -> Any:
    if abs(value - round(value)) < 1e-9:
        return int(round(value))
    return round(value, 6)


def execute_purchase(ctx: BotContext, user_id: Any, price: Any,
                     ok_text: str, fail_text: str) -> dict:
    """Списать price из тега баланса при достаточном остатке.

    success=True — баланс списан (message с подстановкой {price}/{coins});
    success=False — не хватает средств, баланс не тронут.
    """
    try:
        if not user_id:
            return {'success': False,
                    'message': '❌ Покупка доступна только для пользователя'}
        price_value = _purchase_number(price)
        if price_value < 0:
            return {'success': False,
                    'message': '❌ Некорректная цена кнопки'}

        tag = config.PURCHASE_TAG
        row = ctx.storage.get_user(ctx.funnel_id, user_id)
        data: dict = dict(row.get('data') or {}) if row else {}
        balance = _purchase_number(data.get(tag))

        if balance + 1e-9 < price_value:
            text = str(fail_text or '').strip() or \
                '❌ Недостаточно монет: нужно {price}, у вас {coins}'
            text = text.replace('{price}', str(_purchase_normalize(price_value)))
            return {'success': False, 'balance': _purchase_normalize(balance),
                    'message': users.replace_tags_in_text(ctx, text, user_id)}

        data[tag] = _purchase_normalize(balance - price_value)
        ctx.storage.save_user_row(ctx.funnel_id, user_id,
                                  users.get_user_state(ctx, user_id), data)

        text = str(ok_text or '').strip() or \
            '✅ Оплачено! Списано {price}, баланс: {coins}'
        text = text.replace('{price}', str(_purchase_normalize(price_value)))
        return {'success': True, 'balance': data[tag],
                'message': users.replace_tags_in_text(ctx, text, user_id)}
    except Exception as error:  # noqa: BLE001
        logger.error('[executePurchase] %s', error)
        return {'success': False, 'message': f'❌ Ошибка покупки: {error}'}


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

    if func_type == 'calc':
        return execute_calc_formula(ctx, user_id,
                                    func_config['function_param'],
                                    func_config['message'])

    if func_type == 'condition':
        return execute_condition(ctx, user_id, func_config['condition'],
                                 func_config['message'],
                                 func_config['function_param'],
                                 step_yes=func_config.get('step_yes', 0),
                                 step_no=func_config.get('step_no', 0))

    return {'success': False,
            'message': f'❌ Неизвестный тип функции: {func_type}'}
