# ============================================================================
# menu_button.py — синяя кнопка меню («/») и команды бота
#
# Поля воронки (funnels.data, есть в моделях и БД):
#   menu_button_type   — '' (не трогать) | 'command' | 'web_app'
#   menu_button        — для command: список команд меню через запятую
#                        («start: Запустить бота, help: Помощь»);
#                        для web_app: подпись кнопки (1–64 символа)
#   menu_button_command— для web_app: URL открываемого Web App
#
# API: setMyCommands (список команд) + setChatMenuButton (поведение кнопки).
# BotFather не нужен. Аватар/имя бота — только BotFather.
# ============================================================================

from __future__ import annotations

import logging
import re
from typing import Any

from storage import BotContext

logger = logging.getLogger(__name__)

# Имя команды по правилам Telegram: 1–32 символа, латиница/цифры/underscore.
_COMMAND_RE = re.compile(r'^[A-Za-z0-9_]{1,32}$')


def parse_menu_commands(raw: Any) -> list[dict]:
    """Список команд: 'start: Запустить, help: Помощь' → [{command, description}].

    Разделители — запятая и перевод строки (в админке поле многострочное,
    удобно писать одну команду в строку). Без двоеточия описание = имя
    команды. Пустые/невалидные имена пропускаются.
    """
    commands: list[dict] = []
    seen: set[str] = set()
    for part in re.split(r'[,\n]', str(raw or '')):
        part = part.strip()
        if not part:
            continue
        if ':' in part:
            name, description = part.split(':', 1)
            name, description = name.strip(), description.strip()
        else:
            name, description = part, part
        if not _COMMAND_RE.match(name):
            logger.warning('[menu] Пропущена команда с неверным именем: %r',
                           name)
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        commands.append({'command': name, 'description': description or name})
    return commands


def apply_menu_settings(ctx: BotContext) -> bool:
    """Применить настройки синей кнопки меню. True — хотя бы один вызов API."""
    try:
        menu = ctx.menu or {}
        mtype = str(menu.get('type') or '').strip().lower()
        value = str(menu.get('value') or '').strip()
        url = str(menu.get('url') or '').strip()

        if mtype == 'command':
            commands = parse_menu_commands(value)
            if not commands:
                logger.warning('[menu] Нет корректных команд — пропускаем')
                return False
            ctx.tg.call('setMyCommands', {'commands': commands})
            ctx.tg.call('setChatMenuButton',
                        {'menu_button': {'type': 'command'}})
            return True

        if mtype == 'web_app':
            if not url.startswith(('http://', 'https://')):
                logger.warning('[menu] web_app без корректного URL: %r', url)
                return False
            ctx.tg.call('setChatMenuButton', {
                'menu_button': {
                    'type': 'web_app',
                    'text': (value or 'Меню')[:64],
                    'web_app': {'url': url},
                },
            })
            return True

        # '' / неизвестный тип — дефолт Telegram не трогаем.
        return False
    except Exception as error:  # noqa: BLE001
        logger.error('[applyMenuSettings] Error: %s', error)
        return False
