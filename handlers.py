# ============================================================================
# handlers.py — handleMessage / handleCallbackQuery / handleCommand
#               contact / location / showFileId
#
# Порты из apps_script/БОТ_готовый.gs:
#   MessageHandler.handleMessage    :5882
#   handleCallbackQuery             :4583
#   MessageHandler.handleCommand    :6228
#   handleContact / handleLocation  :6142/:6181
#   showFileId                      :6056
# ============================================================================

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

import config
import functions
import keyboards
import steps
import subscription
import users
from keyboards import extract_callback_params
from steps import StepManager
from storage import BotContext

logger = logging.getLogger(__name__)


def _int_or_0(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(str(value))
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# showFileId (:6056) — показ file_id присланного юзером медиа
# ---------------------------------------------------------------------------

def show_file_id(ctx: BotContext, message: dict) -> None:
    try:
        chat_id = message['chat']['id']

        display_value = ''

        if message.get('photo'):
            photo = message['photo'][-1]
            file_id = photo.get('file_id', '')
            display_value = (f'<b>✅ Фото получено!</b>\n\n'
                             f'<b>File ID:</b> <code>{file_id}</code>')
        elif message.get('video'):
            file_id = message['video'].get('file_id', '')
            display_value = (f'<b>✅ Видео получено!</b>\n\n'
                             f'<b>File ID:</b> <code>{file_id}</code>')
        elif message.get('audio'):
            file_id = message['audio'].get('file_id', '')
            display_value = (f'<b>✅ Аудио получено!</b>\n\n'
                             f'<b>File ID:</b> <code>{file_id}</code>')
        elif message.get('document'):
            file_id = message['document'].get('file_id', '')
            display_value = (f'<b>✅ Документ получен!</b>\n\n'
                             f'<b>File ID:</b> <code>{file_id}</code>')
        elif message.get('voice'):
            file_id = message['voice'].get('file_id', '')
            display_value = (f'<b>✅ Голосовое сообщение получено!</b>\n\n'
                             f'<b>File ID:</b> <code>{file_id}</code>')
        elif message.get('sticker'):
            file_id = message['sticker'].get('file_id', '')
            display_value = (f'<b>✅ Стикер получен!</b>\n\n'
                             f'<b>File ID:</b> <code>{file_id}</code>')
        elif message.get('animation'):
            file_id = message['animation'].get('file_id', '')
            display_value = (f'<b>✅ GIF получен!</b>\n\n'
                             f'<b>File ID:</b> <code>{file_id}</code>')
        elif message.get('video_note'):
            file_id = message['video_note'].get('file_id', '')
            display_value = (f'<b>✅ Видеосообщение получено!</b>\n\n'
                             f'<b>File ID:</b> <code>{file_id}</code>')
        elif message.get('dice'):
            dice = message['dice']
            dice_emoji = dice.get('emoji', '🎲')
            dice_value = dice.get('value', 0)

            if dice_emoji == '🎰':
                symbols = steps.get_slot_symbols(dice_value)
                if steps.are_all_symbols_same(symbols):
                    combo_number = steps.get_same_symbol_number(symbols)
                    display_value = (
                        f'<b>✅ Dice ({dice_emoji}) получен!</b>\n\n'
                        f'<b>Результат:</b> {symbols} (комбинация #{combo_number})\n'
                        f'<b>Значение:</b> {dice_value}')
                else:
                    display_value = (
                        f'<b>✅ Dice ({dice_emoji}) получен!</b>\n\n'
                        f'<b>Результат:</b> {symbols} (разные символы)\n'
                        f'<b>Значение:</b> {dice_value}')
            else:
                display_value = (
                    f'<b>✅ Dice ({dice_emoji}) получен!</b>\n\n'
                    f'<b>Значение:</b> {dice_value}')
        else:
            return

        ctx.tg.send_message(chat_id, display_value)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# contact / location (:6142 / :6181)
# ---------------------------------------------------------------------------

def handle_contact(ctx: BotContext, chat_id, user_id: Any, contact: dict) -> None:
    current_step = users.get_user_state(ctx, user_id)
    step = StepManager.get_step_data(ctx, current_step)
    if not step:
        return

    if step.type != 'input':
        return

    type_value = step.type_value
    if type_value and str(type_value).strip() != '':
        contact_data = json.dumps({
            'phone_number': contact.get('phone_number') or '',
            'first_name': contact.get('first_name') or '',
            'last_name': contact.get('last_name') or '',
            'user_id': contact.get('user_id') or '',
        }, ensure_ascii=False, separators=(',', ':'))
        users.save_user_input_response(ctx, user_id, str(type_value).strip(),
                                       contact_data)

    next_step = StepManager.get_next_step(ctx, current_step)
    if next_step > 0:
        users.set_user_state(ctx, user_id, next_step)
        steps.send_step_message(ctx, chat_id, next_step, user_id)
    else:
        steps.complete_funnel(ctx, user_id, chat_id)


def handle_location(ctx: BotContext, chat_id, user_id: Any,
                    location: dict) -> None:
    current_step = users.get_user_state(ctx, user_id)
    step = StepManager.get_step_data(ctx, current_step)
    if not step:
        return

    if step.type != 'input':
        return

    type_value = step.type_value
    if type_value and str(type_value).strip() != '':
        location_data = json.dumps({
            'latitude': location.get('latitude') or 0,
            'longitude': location.get('longitude') or 0,
        }, ensure_ascii=False, separators=(',', ':'))
        users.save_user_input_response(ctx, user_id, str(type_value).strip(),
                                       location_data)

    next_step = StepManager.get_next_step(ctx, current_step)
    if next_step > 0:
        users.set_user_state(ctx, user_id, next_step)
        steps.send_step_message(ctx, chat_id, next_step, user_id)
    else:
        steps.complete_funnel(ctx, user_id, chat_id)


# ---------------------------------------------------------------------------
# handleCommand (:6228)
# ---------------------------------------------------------------------------

def handle_command(ctx: BotContext, chat_id, user_id: Any, command: str) -> None:
    cmd = command.lower()

    if cmd.startswith('/start'):
        referral_link: Optional[str] = None
        # Метка источника: '/start src_ads1' → referral_source;
        # креатив: '/start src_ads1--video1' → + referral_creative.
        source_code, creative_code = \
            users.ReferralManager.extract_source_codes_from_start_command(
                command)
        source_data = {}
        if source_code:
            source_data['referral_source'] = source_code
        if creative_code:
            source_data['referral_creative'] = creative_code

        if ctx.config.get('referral', True):
            referrer_id = users.ReferralManager.extract_referrer_id_from_start_command(
                command)
            if referrer_id:
                referrer_info = users.get_user_info(ctx, referrer_id)
                if referrer_info and referrer_info.get('referral_link'):
                    referral_link = referrer_info['referral_link']
                else:
                    referral_link = users.ReferralManager.generate_referral_link(
                        ctx, referrer_id)

        users.upsert_user(ctx, {'id': user_id}, 1, source_data, referral_link)
        users.set_user_state(ctx, user_id, 1)
        steps.send_step_message(ctx, chat_id, 1, user_id)
        return

    if cmd == '/restart':
        users.set_user_state(ctx, user_id, 1)
        steps.send_step_message(ctx, chat_id, 1, user_id)
    elif cmd == '/back':
        current_step = users.get_user_state(ctx, user_id)
        if current_step > 1:
            users.set_user_state(ctx, user_id, current_step - 1)
            steps.send_step_message(ctx, chat_id, current_step - 1, user_id)
        else:
            ctx.tg.send_message(chat_id, '❌ You are already on the first step')
    elif cmd == '/next':
        current_step = users.get_user_state(ctx, user_id)
        next_step = StepManager.get_next_step(ctx, current_step)
        if next_step > 0:
            users.set_user_state(ctx, user_id, next_step)
            steps.send_step_message(ctx, chat_id, next_step, user_id)
        else:
            ctx.tg.send_message(chat_id, '❌ Next step is not available')
    else:
        ctx.tg.send_message(
            chat_id,
            '<b>❌ Unknown command.</b>\n\n'
            '<b>Available commands:</b>\n'
            '/start, /restart, /back, /next')


# ---------------------------------------------------------------------------
# handleMessage (:5882)
# ---------------------------------------------------------------------------

def _handle_web_app_data(ctx: BotContext, chat_id, user_id: Any,
                         current_step: int, web_app_data: dict) -> None:
    """Mini App вызвал sendData() — переход на шаг одноимённой web app-кнопки."""
    try:
        button_text = str(web_app_data.get('button_text') or '').strip()
        keyboard_data = StepManager.get_inline_keyboard_for_step(
            ctx, current_step)
        if not keyboard_data or not keyboard_data.get('parsedData'):
            return
        for row in keyboard_data['parsedData']['buttons']:
            for button in row:
                if button.get('type') != 'web app':
                    continue
                if str(button.get('text') or '').strip() != button_text:
                    continue
                target = _int_or_0(button.get('step'))
                if target > 0 and target != current_step:
                    delay = _int_or_0(button.get('delay'))
                    if delay > 0:
                        timer = threading.Timer(
                            delay, _delayed_goto,
                            args=(ctx, chat_id, None, current_step, target,
                                  user_id, 'step by step'))
                        timer.daemon = True
                        timer.start()
                    else:
                        users.set_user_state(ctx, user_id, target)
                        steps.send_step_message(ctx, chat_id, target, user_id)
                return
    except Exception as error:  # noqa: BLE001
        logger.error('[handle_web_app_data] %s', error)


def handle_message(ctx: BotContext, message: dict) -> None:
    try:
        chat_id = message['chat']['id']
        text = message.get('text') or ''
        user = message.get('from') or {}
        user_id = user.get('id')

        if not ctx.token or not ctx.funnel_id:
            ctx.tg.send_message(chat_id, '<b>❌ Бот не найден или не настроен</b>')
            return

        if message['chat'].get('type') != 'private':
            if text and text.startswith('/'):
                ctx.tg.send_message(
                    chat_id,
                    '<b>🤖 Бот для личных сообщений</b>\n\n'
                    f'Напишите мне в личку: @{users.get_bot_username(ctx)}',
                    {'reply_to_message_id': message.get('message_id')})
            return

        current_step = users.get_user_state(ctx, user_id)

        referral_link: Optional[str] = None
        # Метка источника: '/start src_ads1' → referral_source;
        # креатив: '/start src_ads1--video1' → + referral_creative.
        source_code, creative_code = \
            users.ReferralManager.extract_source_codes_from_start_command(
                text)
        source_data = {}
        if source_code:
            source_data['referral_source'] = source_code
        if creative_code:
            source_data['referral_creative'] = creative_code
        if text.startswith('/start') and ctx.config.get('referral', True):
            referrer_id = users.ReferralManager.extract_referrer_id_from_start_command(
                text)
            if referrer_id:
                referrer_info = users.ReferralManager.find_referrer_by_id(
                    ctx, referrer_id)
                if referrer_info and referrer_info.get('referral_link'):
                    referral_link = referrer_info['referral_link']

        users.upsert_user(ctx, user, current_step, source_data, referral_link)

        if text.startswith('/start'):
            users.set_user_state(ctx, user_id, 1)
            steps.send_step_message(ctx, chat_id, 1, user_id)
            return

        # Web App вызвал sendData() → переход на шаг кнопки, если задан.
        web_app_data = message.get('web_app_data')
        if web_app_data:
            _handle_web_app_data(ctx, chat_id, user_id, current_step,
                                 web_app_data)
            return

        step = StepManager.get_step_data(ctx, current_step)

        if step:
            if step.type == 'input' and text and text.strip() != '':
                if step.type_value and str(step.type_value).strip() != '':
                    users.save_user_input_response(
                        ctx, user_id, str(step.type_value).strip(), text.strip())

                next_step = StepManager.get_next_step(ctx, current_step)
                if next_step > 0:
                    users.set_user_state(ctx, user_id, next_step)
                    steps.send_step_message(ctx, chat_id, next_step, user_id)
                else:
                    steps.complete_funnel(ctx, user_id, chat_id)
                return

        # Сопоставление текста с reply-кнопками текущего шага
        keyboard_data = StepManager.get_keyboard_for_step(ctx, current_step)
        if keyboard_data and keyboard_data.get('parsedData'):
            button_found = False
            for row in keyboard_data['parsedData']['buttons']:
                for button in row:
                    if button.get('text') == text:
                        button_found = True
                        if button.get('type') == 'command' and button.get('command'):
                            command = button['command']
                            if command == 'next':
                                next_step = StepManager.get_next_step(ctx, current_step)
                                if next_step > 0:
                                    users.set_user_state(ctx, user_id, next_step)
                                    steps.send_step_message(ctx, chat_id, next_step,
                                                            user_id)
                                else:
                                    steps.complete_funnel(ctx, user_id, chat_id)
                            elif command == 'back' and current_step > 1:
                                users.set_user_state(ctx, user_id, current_step - 1)
                                steps.send_step_message(ctx, chat_id,
                                                        current_step - 1, user_id)
                            elif command in ('restart', 'start'):
                                users.set_user_state(ctx, user_id, 1)
                                steps.send_step_message(ctx, chat_id, 1, user_id)
                            else:
                                result = functions.execute_function(
                                    ctx, command, user_id, chat_id)
                                if result.get('message'):
                                    ctx.tg.send_message(chat_id, result['message'])
                                # Шаг: из ветки condition иначе из поля кнопки.
                                target = _int_or_0(result.get('step')) \
                                    or _int_or_0(button.get('step'))
                                if target > 0 and target != current_step:
                                    users.set_user_state(ctx, user_id, target)
                                    steps.send_step_message(
                                        ctx, chat_id, target, user_id)
                        break
                if button_found:
                    break

            if button_found:
                return

        if any(message.get(kind) for kind in (
                'photo', 'video', 'document', 'audio', 'voice', 'video_note',
                'animation', 'sticker')):
            show_file_id(ctx, message)
            return

        if message.get('contact'):
            handle_contact(ctx, chat_id, user_id, message['contact'])
            return

        if message.get('location'):
            handle_location(ctx, chat_id, user_id, message['location'])
            return

        if text.startswith('/'):
            handle_command(ctx, chat_id, user_id, text)
            return

        if current_step > 0:
            if step:
                if step.type != 'input' and text.strip() != '':
                    fixed_response = functions.get_system_info_message(ctx)
                    if fixed_response and ctx.config.get('systemmessages', True):
                        processed = users.replace_tags_in_text(
                            ctx, fixed_response, user_id)
                        ctx.tg.send_message(chat_id, f'<b>{processed}</b>')
        else:
            final_response = functions.get_system_final_message(ctx)
            if final_response and ctx.config.get('systemmessages', True):
                processed = users.replace_tags_in_text(ctx, final_response, user_id)
                ctx.tg.send_message(chat_id, f'<b>{processed}</b>')
    except Exception as error:  # noqa: BLE001
        logger.error('[handleMessage] %s', error)


# ---------------------------------------------------------------------------
# handleCallbackQuery (:4583)
# ---------------------------------------------------------------------------

def _button_method(keyboard_data: Optional[dict], row: int, col: int) -> str:
    if keyboard_data and keyboard_data.get('parsedData'):
        buttons = keyboard_data['parsedData'].get('buttons') or []
        if row < len(buttons) and col < len(buttons[row]):
            return buttons[row][col].get('method') or 'step by step'
    return 'step by step'


def _button_delay(keyboard_data: Optional[dict], row: int, col: int) -> int:
    """Задержка кнопки (сек) перед переходом; 0 — без задержки."""
    if keyboard_data and keyboard_data.get('parsedData'):
        buttons = keyboard_data['parsedData'].get('buttons') or []
        if row < len(buttons) and col < len(buttons[row]):
            return _int_or_0(buttons[row][col].get('delay'))
    return 0


def _button_purchase(keyboard_data: Optional[dict], row: int,
                     col: int) -> tuple[Any, int]:
    """Цена и целевой шаг кнопки purchase из parsedData; (None, 0) — нет."""
    if keyboard_data and keyboard_data.get('parsedData'):
        buttons = keyboard_data['parsedData'].get('buttons') or []
        if row < len(buttons) and col < len(buttons[row]):
            button = buttons[row][col]
            return button.get('value'), _int_or_0(button.get('step'))
    return None, 0


def _delayed_goto(ctx: BotContext, chat_id, message_id: Optional[int],
                  from_step: int, target: int, user_id: Any,
                  method: str) -> None:
    """Переход после задержки кнопки: только если пользователь не ушёл
    с исходного шага (пока ждали, он мог нажать другую кнопку)."""
    try:
        if users.get_user_state(ctx, user_id) != from_step:
            return
        users.set_user_state(ctx, user_id, target)
        if method == 'update step' and message_id:
            steps.update_step_message(ctx, chat_id, message_id,
                                      target, user_id)
        else:
            steps.send_step_message(ctx, chat_id, target, user_id)
    except Exception as error:  # noqa: BLE001
        logger.error('[delayed_goto] %s', error)


def handle_callback_query(ctx: BotContext, callback_query: dict) -> None:
    try:
        query_id = callback_query.get('id')
        user_id = (callback_query.get('from') or {}).get('id')
        message = callback_query.get('message') or {}
        chat_id = (message.get('chat') or {}).get('id')
        message_id = message.get('message_id')
        data = callback_query.get('data')

        if not data or not chat_id:
            ctx.tg.answer_callback_query(query_id)
            return

        current_step = users.get_user_state(ctx, user_id)
        callback_processed = False

        # -- check_sub_*: проверка подписки на канал -------------------------
        if data.startswith(config.CALLBACK_PREFIXES['check_sub']):
            params = extract_callback_params(data)
            check_step_number = _int_or_0(params.get('check'))
            row = _int_or_0(params.get('row'))
            col = _int_or_0(params.get('col'))
            action = params.get('action') or 'keep'
            channel = params.get('channel') or ''

            if not channel or channel.strip() == '':
                ctx.tg.answer_callback_query(query_id,
                                             '❌ Ошибка: канал не указан', True)
                return

            keyboard_data = StepManager.get_inline_keyboard_for_step(
                ctx, current_step)
            if not keyboard_data:
                ctx.tg.answer_callback_query(query_id,
                                             '❌ Ошибка: данные не найдены', True)
                return

            subscription_message = (keyboard_data.get('subscriptionMessage')
                                    or '✅ Отлично! Вы подписаны.')
            no_subscription_message = (
                keyboard_data.get('noSubscriptionMessage')
                or '❌ Подпишитесь на канал и нажмите кнопку снова.')

            is_subscribed = subscription.check_subscription(
                ctx.tg, user_id, channel)

            if is_subscribed:
                # Всплывает поп-ап с текстом из формы («Сообщение при
                # успешной подписке»); сообщений в чат не шлём.
                # Лимит Telegram на alert — 200 символов.
                alert_text = subscription_message
                if len(alert_text) > 200:
                    alert_text = alert_text[:197] + '…'
                ctx.tg.answer_callback_query(query_id, alert_text, True)

                if check_step_number > 0 and check_step_number != current_step:
                    users.set_user_state(ctx, user_id, check_step_number)
                    method = _button_method(keyboard_data, row, col)
                    if method == 'update step':
                        steps.update_step_message(ctx, chat_id, message_id,
                                                  check_step_number, user_id)
                    else:
                        steps.send_step_message(ctx, chat_id, check_step_number,
                                                user_id)
                elif check_step_number == 0:
                    keyboards.process_button_action(
                        ctx.tg, keyboard_data, chat_id, message_id,
                        action, row, col)

                callback_processed = True
            else:
                # Тот же поп-ап с текстом из формы («Сообщение при
                # отсутствии подписки»); сообщений в чат не шлём.
                alert_text = no_subscription_message.replace(
                    '{channel}', channel)
                if len(alert_text) > 200:
                    alert_text = alert_text[:197] + '…'
                ctx.tg.answer_callback_query(query_id, alert_text, True)
                callback_processed = True
            return

        # -- buy_*: покупка по кнопке (проверка баланса + списание) ---------
        if data.startswith(config.CALLBACK_PREFIXES['buy']):
            params = extract_callback_params(data)
            row = _int_or_0(params.get('row'))
            col = _int_or_0(params.get('col'))
            action = params.get('action') or 'keep'

            keyboard_data = StepManager.get_inline_keyboard_for_step(
                ctx, current_step)
            price, target_step = _button_purchase(keyboard_data, row, col)
            if price is None:
                ctx.tg.answer_callback_query(query_id,
                                             '❌ Ошибка: цена не найдена', True)
                return

            purchase_texts = (keyboard_data or {}).get('purchaseButtons') or {}
            button_texts = purchase_texts.get(f'{row}_{col}') or {}
            result = functions.execute_purchase(
                ctx, user_id, price,
                str(button_texts.get('ok') or ''),
                str(button_texts.get('fail') or ''))

            message_text = str(result.get('message') or '')
            if result.get('success'):
                if message_text:
                    ctx.tg.send_message(chat_id, message_text)
                keyboards.process_button_action(ctx.tg, keyboard_data,
                                                chat_id, message_id,
                                                action, row, col)
                if target_step > 0 and target_step != current_step:
                    method = _button_method(keyboard_data, row, col)
                    users.set_user_state(ctx, user_id, target_step)
                    if method == 'update step' and message_id:
                        steps.update_step_message(ctx, chat_id, message_id,
                                                  target_step, user_id)
                    else:
                        steps.send_step_message(ctx, chat_id, target_step,
                                                user_id)
                ctx.tg.answer_callback_query(query_id, '✅ Оплачено!', False)
            else:
                if message_text:
                    ctx.tg.send_message(chat_id, message_text)
                # Alert обрезает длинный текст — короткая общая формулировка,
                # детали ушли сообщением выше.
                ctx.tg.answer_callback_query(query_id, '❌ Покупка не прошла',
                                             True)
            callback_processed = True
            return

        # -- step_*: переход на шаг ----------------------------------------
        if data.startswith(config.CALLBACK_PREFIXES['step']):
            params = extract_callback_params(data)
            button_step_number = _int_or_0(params.get('step'))
            row = _int_or_0(params.get('row'))
            col = _int_or_0(params.get('col'))
            action = params.get('action') or 'keep'

            keyboard_data = StepManager.get_inline_keyboard_for_step(
                ctx, current_step)
            keyboards.process_button_action(ctx.tg, keyboard_data, chat_id,
                                            message_id, action, row, col)

            if button_step_number > 0 and button_step_number != current_step:
                method = _button_method(keyboard_data, row, col)
                users.set_user_state(ctx, user_id, button_step_number)
                if method == 'update step':
                    steps.update_step_message(ctx, chat_id, message_id,
                                              button_step_number, user_id)
                else:
                    steps.send_step_message(ctx, chat_id, button_step_number,
                                            user_id)

            ctx.tg.answer_callback_query(query_id)
            callback_processed = True

        # -- cmd_*: команды и функции ---------------------------------------
        elif data.startswith(config.CALLBACK_PREFIXES['cmd']):
            params = extract_callback_params(data)
            command = params.get('cmd') or ''
            row = _int_or_0(params.get('row'))
            col = _int_or_0(params.get('col'))
            action = params.get('action') or 'keep'

            keyboard_data = StepManager.get_inline_keyboard_for_step(
                ctx, current_step)
            keyboards.process_button_action(ctx.tg, keyboard_data, chat_id,
                                            message_id, action, row, col)

            if command == 'next':
                next_step = StepManager.get_next_step(ctx, current_step)
                if next_step > 0:
                    users.set_user_state(ctx, user_id, next_step)
                    steps.send_step_message(ctx, chat_id, next_step, user_id)
                else:
                    steps.complete_funnel(ctx, user_id, chat_id)
            elif command == 'back' and current_step > 1:
                users.set_user_state(ctx, user_id, current_step - 1)
                steps.send_step_message(ctx, chat_id, current_step - 1, user_id)
            elif command in ('restart', 'start'):
                users.set_user_state(ctx, user_id, 1)
                steps.send_step_message(ctx, chat_id, 1, user_id)
            else:
                ctx.tg.answer_callback_query(query_id, '⏳ Выполняется...', False)
                result = functions.execute_function(ctx, command, user_id, chat_id)
                if result.get('message'):
                    ctx.tg.send_message(chat_id, result['message'])
                # Шаг: ветка condition приоритетнее, иначе поле кнопки Command.
                target_step = _int_or_0(result.get('step')) \
                    or _int_or_0(params.get('step'))
                if target_step > 0 and target_step != current_step:
                    method = _button_method(keyboard_data, row, col)
                    delay = _button_delay(keyboard_data, row, col)
                    if delay > 0:
                        timer = threading.Timer(
                            delay, _delayed_goto,
                            args=(ctx, chat_id, message_id, current_step,
                                  target_step, user_id, method))
                        timer.daemon = True
                        timer.start()
                    else:
                        users.set_user_state(ctx, user_id, target_step)
                        if method == 'update step' and message_id:
                            steps.update_step_message(ctx, chat_id, message_id,
                                                      target_step, user_id)
                        else:
                            steps.send_step_message(ctx, chat_id, target_step,
                                                    user_id)

            ctx.tg.answer_callback_query(query_id)
            callback_processed = True

        if not callback_processed:
            ctx.tg.answer_callback_query(query_id)

    except Exception as error:  # noqa: BLE001
        logger.error('[handleCallbackQuery] %s', error)
        try:
            if callback_query and callback_query.get('id'):
                ctx.tg.answer_callback_query(callback_query['id'])
        except Exception:  # noqa: BLE001
            pass
