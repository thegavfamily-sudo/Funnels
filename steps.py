# ============================================================================
# steps.py — StepManager + sendStepMessage/updateStepMessage + медиа/баркод/
#            опросы/кости
#
# Вся работа с шагами идёт через step_model.Step (типизированные поля):
# строки старой таблицы парсятся ОДИН РАЗ при загрузке воронки
# (storage.build_step_entry → step_model.build_step), send-time не парсит.
#
# Порты из apps_script/БОТ_готовый.gs:
#   Utils слоты/кости            :451-565
#   Utils barcode/QR             :1120-1381
#   StepManager                  :3609
#   sendMediaMessage             :4244
#   sendBarcodeMessage           :4439
#   Poll/Quiz/Dice managers      :1730-2121
#   MessageHandler.sendStepMessage :5503
#   updateStepMessage            :5664
#   completeFunnel               :6218
# ============================================================================

from __future__ import annotations

import base64
import logging
import random
import threading
import time
from typing import Any, Optional

import config
import effects
import functions
import keyboards
import users
from step_model import (Step, advent_current_day, split_advent_prize,
                        valid_http_url)
from storage import BotContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Кости / слоты: рантайм значения броска (конфиг ветвления — в Step.dice)
# ---------------------------------------------------------------------------

# Длительность анимации sendDice в клиенте Telegram (сек) — с запасом, чтобы
# переход (следующий шаг) не приходил раньше её окончания; далее
# process_dice_result добавляет ~1 с паузы, как в предпросмотре.
# Слоты крутятся дольше.
DICE_ANIMATION_SECONDS = {'🎰': 3.5}
DICE_ANIMATION_DEFAULT_SECONDS = 2.0


def dice_animation_seconds(emoji: Any) -> float:
    return DICE_ANIMATION_SECONDS.get(emoji, DICE_ANIMATION_DEFAULT_SECONDS)


def resolve_dice_next_step(step: Step, dice_value: Any) -> Optional[int]:
    """Явный маппинг «исход → шаг» из конфига dice.

    None — маппинга нет или у исхода нет пары (вызывающий идёт на следующий
    шаг); int — ответ конфига, 0 = без перехода (воронка завершается), как
    в форме админки («Без перехода»).
    """
    logic_values = step.dice.logic_values
    step_mapping = step.dice.steps
    if not logic_values or not step_mapping \
            or len(logic_values) != len(step_mapping):
        return None
    lookup = dice_value
    if step.dice.emoji == '🎰':
        # 🎰: логическое значение — номер комбинации (0 = разные символы).
        symbols = get_slot_symbols(dice_value)
        lookup = (get_same_symbol_number(symbols)
                  if are_all_symbols_same(symbols) else 0)
    if lookup not in logic_values:
        return None
    return step_mapping[logic_values.index(lookup)]


def get_slot_symbols(dice_value: int) -> str:
    if dice_value < 1 or dice_value > 64:
        return '❓❓❓'

    for symbol, values in config.SAME_SYMBOLS_VALUES.items():
        if dice_value in values:
            return symbol

    different_symbol_combinations = [
        '🍒🍋🔔', '🍋🍒7️⃣', '🔔7️⃣🍒', '7️⃣🔔🍋',
        '🍒🔔7️⃣', '🍋7️⃣🔔', '🔔🍋7️⃣', '7️⃣🍒🔔',
        '🍒🍒🍋', '🍋🍋🔔', '🔔🔔7️⃣', '7️⃣7️⃣🍒',
        '🍒🍋🍒', '🍋🔔🍋', '🔔7️⃣🔔', '7️⃣🍒7️⃣',
    ]
    index = (dice_value - 1) % len(different_symbol_combinations)
    return different_symbol_combinations[index]


def are_all_symbols_same(symbols: Any) -> bool:
    if not symbols or not isinstance(symbols, str):
        return False
    return symbols in ('🍒🍒🍒', '🍋🍋🍋', '🔔🔔🔔', '7️⃣7️⃣7️⃣')


def get_same_symbol_number(symbols: str) -> int:
    return config.SYMBOL_TO_NUMBER.get(symbols, 0)


# ---------------------------------------------------------------------------
# Barcode / QR: сборка URL и данные для отправки
# ---------------------------------------------------------------------------

def generate_barcode_url(data: str, options: Optional[dict] = None,
                         qr_config: Optional[dict] = None) -> Optional[str]:
    try:
        cfg = config.BARCODE_CONFIG
        options = options or {}
        qr_config = qr_config or {}

        qr_data = data or f'USER_{int(time.time() * 1000)}'
        from urllib.parse import quote
        qr_url = f"{cfg['BASE_URL']}?text={quote(str(qr_data), safe='')}"

        size = qr_config.get('size') or options.get('size') or cfg['DEFAULT_SIZE']
        qr_url += f'&size={size}'

        margin = qr_config.get('margin') or options.get('margin') or cfg['DEFAULT_MARGIN']
        qr_url += f'&margin={margin}'

        fmt = qr_config.get('format') or options.get('format') or cfg['DEFAULT_FORMAT']
        qr_url += f'&format={fmt}'

        dark_color = (qr_config.get('dark') or qr_config.get('color')
                      or options.get('color') or options.get('dark')
                      or cfg['DEFAULT_FG_COLOR'])
        light_color = (qr_config.get('light') or qr_config.get('bgcolor')
                       or options.get('bgColor') or cfg['DEFAULT_BG_COLOR'])

        if dark_color:
            qr_url += f"&dark={str(dark_color).replace('#', '')}"
        if light_color:
            qr_url += f"&light={str(light_color).replace('#', '')}"

        ec_level = (qr_config.get('ecl') or qr_config.get('ecLevel')
                    or options.get('ecl') or options.get('ecLevel')
                    or cfg['DEFAULT_EC_LEVEL'])
        qr_url += f'&ecLevel={ec_level}'

        center_image_url = (qr_config.get('centerImageUrl')
                            or options.get('logoUrl') or options.get('logo')
                            or cfg['DEFAULT_LOGO_URL'])
        if center_image_url and str(center_image_url).strip() != '' \
                and str(center_image_url).lower() not in ('none', 'false', 'null'):
            qr_url += f'&centerImageUrl={quote(str(center_image_url), safe="")}'
            ratio = (qr_config.get('centerImageSizeRatio')
                     or options.get('logoSizeRatio')
                     or cfg['DEFAULT_LOGO_SIZE_RATIO'])
            qr_url += f'&centerImageSizeRatio={ratio}'

        caption = qr_config.get('caption')
        if caption and str(caption).strip() != '':
            qr_url += f'&caption={quote(str(caption).strip(), safe="")}'
            if qr_config.get('captionFontFamily'):
                qr_url += (f"&captionFontFamily="
                           f"{quote(str(qr_config['captionFontFamily']), safe='')}")
            if qr_config.get('captionFontSize'):
                qr_url += f"&captionFontSize={qr_config['captionFontSize']}"
            if qr_config.get('captionFontColor'):
                font_color = str(qr_config['captionFontColor']).replace('#', '')
                qr_url += f'&captionFontColor={font_color}'

        qr_url += f'&t={int(time.time() * 1000)}'
        return qr_url
    except Exception as error:  # noqa: BLE001
        logger.error('Error generating barcode URL: %s', error)
        return None


def parse_barcode_data(ctx: BotContext, step: Step,
                       user_id: Any = None) -> dict:
    """Данные QR: подстановка {тегов} и генерация id, если данных нет.

    Настройки картинки (options/qr_config) уже разобраны в Step.barcode.
    """
    try:
        barcode = step.barcode
        barcode_data = str(barcode.data) if barcode else ''
        if user_id and '{' in barcode_data:
            barcode_data = users.replace_tags_in_text(ctx, barcode_data, user_id)

        if not barcode_data or barcode_data.strip() == '':
            user_info = users.get_user_info(ctx, user_id) if user_id else None
            unique_id = (user_info or {}).get('user_id') or (user_id or '')
            timestamp = int(time.time() * 1000)
            random_str = format(random.randint(0, 36 ** 9 - 1), 'X').upper()[:9]
            barcode_data = f'USER_{unique_id}_{timestamp}_{random_str}'

        return {
            'data': barcode_data,
            'options': dict(barcode.options) if barcode else {},
            'qrConfig': dict(barcode.qr_config) if barcode else {},
        }
    except Exception as error:  # noqa: BLE001
        logger.error('Error parsing barcode data: %s', error)
        return {'data': f'USER_{user_id}_{int(time.time() * 1000)}',
                'options': {}, 'qrConfig': {}}


def download_image_as_base64(image_url: str) -> Optional[str]:
    try:
        if not image_url:
            return None
        import requests
        response = requests.get(image_url, timeout=30)
        if response.status_code == 200:
            mime_type = response.headers.get('Content-Type', 'image/png')
            b64 = base64.b64encode(response.content).decode('ascii')
            return f'data:{mime_type};base64,{b64}'
        return None
    except Exception as error:  # noqa: BLE001
        logger.error('Error downloading image: %s', error)
        return None


# ---------------------------------------------------------------------------
# StepManager (:3609)
# ---------------------------------------------------------------------------

class StepManager:
    @staticmethod
    def get_step_data(ctx: BotContext, step_number: int) -> Optional[Step]:
        if not ctx.funnel_id:
            return None
        return ctx.storage.get_step(ctx.funnel_id, step_number)

    @staticmethod
    def get_next_step(ctx: BotContext, current_step: int) -> int:
        """step.next (>0), иначе current+1 (если есть), иначе 0 = конец."""
        step = StepManager.get_step_data(ctx, current_step)
        if not step:
            return 0
        if step.next is not None and step.next > 0:
            return step.next
        next_sequential = current_step + 1
        if StepManager.get_step_data(ctx, next_sequential):
            return next_sequential
        return 0

    @staticmethod
    def get_keyboard_for_step(ctx: BotContext,
                              step_number: int) -> Optional[dict]:
        step = StepManager.get_step_data(ctx, step_number)
        return step.reply_keyboard if step else None

    @staticmethod
    def get_inline_keyboard_for_step(ctx: BotContext,
                                     step_number: int) -> Optional[dict]:
        step = StepManager.get_step_data(ctx, step_number)
        return step.inline_keyboard if step else None


def build_reply_keyboard(ctx: BotContext, step_number: int,
                         user_role: str = '') -> Optional[dict]:
    """Порт buildReplyKeyboard (:3907): role-фильтр клавиатуры при отправке."""
    keyboard_data = StepManager.get_keyboard_for_step(ctx, step_number)
    if not keyboard_data or not keyboard_data.get('parsedData'):
        return None
    return keyboards.build_regular_keyboard_from_parsed_data(
        keyboard_data['parsedData'], user_role)


# ---------------------------------------------------------------------------
# Игровые кэши: dice / poll / quiz (TTL 5 мин — в Storage)
# ---------------------------------------------------------------------------

def _now_ms() -> float:
    return time.time() * 1000


def add_dice_result(ctx: BotContext, message_id: Any, user_id: Any, chat_id: Any,
                    dice_value: Any, step: Step) -> None:
    ctx.storage.dice_results[message_id] = {
        'userId': user_id, 'chatId': chat_id, 'diceValue': dice_value,
        'step': step,
        'timestamp': _now_ms(), 'processed': False,
    }
    ctx.storage.cleanup_game_caches()


def add_poll_result(ctx: BotContext, poll_id: str, user_id: Any, chat_id: Any,
                    step: Step) -> None:
    cache = ctx.storage.poll_results if step.type == 'poll' \
        else ctx.storage.quiz_results
    cache[poll_id] = {
        'userId': user_id, 'chatId': chat_id, 'selectedOption': -1,
        'step': step,
        'timestamp': _now_ms(), 'processed': False,
    }
    ctx.storage.cleanup_game_caches()


def _run_step_calc(ctx: BotContext, chat_id: Any, user_id: Any,
                   step: Optional[Step]) -> None:
    """Формула calc из шага (calc_formula) — авто-расчёт после
    броска/ответа, до отправки следующего шага. Успех не шлёт отдельное
    сообщение (следующий шаг покажет новые теги); ошибку показываем,
    чтобы её не было незаметно."""
    formula = (step.calc_formula if step else '') or ''
    if not formula:
        return
    result = functions.execute_calc_formula(ctx, user_id, formula, '')
    if not result.get('success') and result.get('message'):
        ctx.tg.send_message(chat_id, result['message'])


def process_dice_result(ctx: BotContext, message_id: Any) -> bool:
    """Порт DiceManager.processDiceResult (:1807)."""
    try:
        dice_result = ctx.storage.dice_results.get(message_id)
        if not dice_result or dice_result.get('processed'):
            return False

        user_id = dice_result['userId']
        chat_id = dice_result['chatId']
        dice_value = dice_result['diceValue']
        step: Step = dice_result['step']

        dice_emoji = step.dice.emoji

        # Результат броска → теги {dice}/{dice_emoji}/… для формул calc.
        users.save_dice_tags(ctx, user_id, dice_emoji, dice_value)
        # Формула из шага (calc_formula) — авто-расчёт по тегам.
        _run_step_calc(ctx, chat_id, user_id, step)

        # Функции trigger=dice — после тегов; ветка condition перекрывает next.
        trig_msgs, trig_branch = functions.run_functions_for_trigger(
            ctx, user_id, 'dice')
        for msg in trig_msgs:
            ctx.tg.send_message(chat_id, msg)

        # Явный маппинг «исход → шаг»: 0 = без перехода (конец воронки).
        # Маппинга нет или у исхода нет пары — как раньше, следующий шаг.
        resolved = resolve_dice_next_step(step, dice_value)
        if trig_branch > 0:
            next_step = trig_branch
        elif resolved is not None:
            next_step = resolved
        else:
            next_step = StepManager.get_next_step(ctx, step.number)

        dice_result['processed'] = True
        # Пауза после броска: анимацию handle_dice_message уже выждал,
        # здесь ~1 с до отправки следующего шага (как в предпросмотре).
        time.sleep(1)

        if next_step > 0:
            users.set_user_state(ctx, user_id, next_step)
            send_step_message(ctx, chat_id, next_step, user_id)
            return True
        complete_funnel(ctx, user_id, chat_id)
        return False
    except Exception as error:  # noqa: BLE001
        logger.error('[processDiceResult] %s', error)
        return False


def check_dice_result(ctx: BotContext, message_id: Any, chat_id: Any) -> None:
    """Порт checkDiceResult (:5324): фолбэк рандомного значения 1..6."""
    try:
        dice_result = ctx.storage.dice_results.get(message_id)
        if not dice_result:
            return
        if dice_result.get('diceValue') and dice_result['diceValue'] > 0:
            process_dice_result(ctx, message_id)
        else:
            random_value = random.randint(1, 6)
            add_dice_result(ctx, message_id, dice_result['userId'],
                            dice_result['chatId'], random_value,
                            dice_result['step'])
            time.sleep(1)
            process_dice_result(ctx, message_id)
    except Exception:  # noqa: BLE001
        pass


def process_poll_result(ctx: BotContext, poll_id: str, target: str = 'poll') -> bool:
    """Порт PollManager.processPollResult / QuizManager.processQuizResult."""
    try:
        cache = ctx.storage.poll_results if target == 'poll' else ctx.storage.quiz_results
        poll_result = cache.get(poll_id)
        if not poll_result or poll_result.get('processed'):
            return False

        selected_option = poll_result.get('selectedOption', -1)
        step: Step = poll_result['step']
        cfg = step.poll if target == 'poll' else step.quiz
        logic_values = cfg.logic_values if cfg else []
        steps_list = cfg.steps if cfg else []

        # Ответ poll/quiz → теги {poll_answer}/{quiz_correct}/… для формул calc.
        users.save_poll_tags(ctx, poll_result['userId'], target,
                             selected_option, logic_values, step.quiz)
        # Формула из шага (calc_formula) — авто-расчёт по тегам.
        _run_step_calc(ctx, poll_result['chatId'], poll_result['userId'], step)

        # Функции trigger=poll/quiz — после тегов; ветка перекрывает next.
        trig_msgs, trig_branch = functions.run_functions_for_trigger(
            ctx, poll_result['userId'], target)
        for msg in trig_msgs:
            ctx.tg.send_message(poll_result['chatId'], msg)

        # Режимы формы как у dice: no_transition («Без перехода» из чипов)
        # — воронка завершается; иначе маппинг «ответ → шаг» как раньше
        # (>0 — переход, 0/нет пары — следующий шаг).
        no_transition = bool(cfg is not None and cfg.no_transition)
        next_step = 0
        if trig_branch > 0:
            next_step = trig_branch
        elif not no_transition:
            if logic_values and steps_list \
                    and 0 <= selected_option < len(logic_values):
                # Как в JS: steps[selected] может отсутствовать → undefined → 0.
                next_step = (steps_list[selected_option]
                             if selected_option < len(steps_list) else 0) or 0
            if next_step <= 0:
                next_step = StepManager.get_next_step(ctx, step.number)

        poll_result['processed'] = True
        time.sleep(0.5)

        if next_step > 0:
            users.set_user_state(ctx, poll_result['userId'], next_step)
            send_step_message(ctx, poll_result['chatId'], next_step,
                              poll_result['userId'])
            return True
        complete_funnel(ctx, poll_result['userId'], poll_result['chatId'])
        return False
    except Exception as error:  # noqa: BLE001
        logger.error('[processPollResult] %s', error)
        return False


# ---------------------------------------------------------------------------
# Отправка медиа (sendMediaMessage :4244 и др.)
# ---------------------------------------------------------------------------

_MEDIA_METHOD_MAP = {
    'photo': 'sendPhoto',
    'video': 'sendVideo',
    'audio': 'sendAudio',
    'document': 'sendDocument',
    'voice': 'sendVoice',
    'video_note': 'sendVideoNote',
    'animation': 'sendAnimation',
    'sticker': 'sendSticker',
    'media_group': 'sendDocument',
}

# Подпись (caption) принимают только эти методы Bot API — у sendSticker и
# sendVideoNote параметра caption нет (сверено с сигнатурами python-telegram-bot
# и @types/node-telegram-bot-api); им caption слать нельзя.
CAPTION_MEDIA_TYPES = frozenset({
    'photo', 'video', 'audio', 'document', 'animation', 'voice',
    'media_group',  # одиночный файл уходит sendDocument
})


def _media_payload_field(media_type: str) -> str:
    if media_type == 'photo':
        return 'photo'
    if media_type == 'media_group':
        return 'document'
    return media_type


def _guess_url_media_type(url: str) -> str:
    """Тип элемента media_group по расширению URL (фото/видео/аудио/документ)."""
    from urllib.parse import urlparse
    path = urlparse(url).path.lower()
    for ext in ('.mp4', '.webm', '.mov', '.m4v', '.avi', '.mkv'):
        if path.endswith(ext):
            return 'video'
    for ext in ('.mp3', '.ogg', '.m4a', '.wav', '.aac', '.flac'):
        if path.endswith(ext):
            return 'audio'
    for ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
        if path.endswith(ext):
            return 'photo'
    return 'document'


def send_media_items_individually(ctx: BotContext, chat_id, media_items: list,
                                  caption: str, options: Optional[dict] = None) -> Any:
    """Порт sendMediaItemsIndividually (:4200)."""
    options = options or {}
    last_message_id = None
    total = len(media_items)

    for index, item in enumerate(media_items):
        media_type = item.get('type', 'document')
        media_source = item.get('media')
        payload: dict[str, Any] = {'chat_id': chat_id}
        payload[_media_payload_field(media_type)] = media_source

        if index == total - 1 and caption and caption.strip() != '':
            payload['caption'] = caption.strip()
            payload['parse_mode'] = 'HTML'
        if index == total - 1 and options.get('reply_markup'):
            payload['reply_markup'] = options['reply_markup']

        method = _MEDIA_METHOD_MAP.get(media_type, 'sendMessage')
        response = ctx.tg.send_request(method, payload)
        if response.get('ok'):
            last_message_id = response['result']['message_id']

    return last_message_id


def send_media_group_universal(ctx: BotContext, chat_id, media_items: list,
                               caption: str, options: Optional[dict] = None) -> Any:
    """Порт sendMediaGroupUniversal (:4172) + фолбэк поштучной отправки."""
    options = options or {}
    try:
        if not media_items:
            return None

        result = ctx.tg.send_media_group(chat_id, media_items)
        if result:
            if options.get('reply_markup'):
                keyboard_message = (caption if caption and caption.strip() != ''
                                    else f'📁 Медиагруппа ({len(media_items)} файлов)')
                ctx.tg.send_message(chat_id, keyboard_message,
                                    {'parse_mode': 'HTML',
                                     'reply_markup': options['reply_markup']})
            return result[0].get('message_id') if result else None
        return send_media_items_individually(ctx, chat_id, media_items,
                                             caption, options)
    except Exception as error:  # noqa: BLE001
        logger.error('sendMediaGroupUniversal: %s', error)
        return None


def send_media_message(ctx: BotContext, chat_id, step: Step,
                       media_type: str, options: Optional[dict] = None,
                       caption: Optional[str] = None) -> Any:
    """Порт sendMediaMessage (:4244).

    Файлы уже разобраны в step.file_urls/step.file_ids при загрузке;
    caption — текст с подставленными тегами (готовит send_step_message),
    по умолчанию — сырой step.text.
    """
    options = options or {}
    try:
        if caption is None:
            caption = step.text
        urls = list(step.file_urls)
        file_ids = list(step.file_ids)

        total_files = len(urls) + len(file_ids)

        if total_files == 0:
            content = step.text
            if content and str(content).startswith(('http://', 'https://')) \
                    and valid_http_url(str(content)):
                message_id = ctx.tg.send_photo(chat_id, content, caption, options)
                if message_id:
                    return message_id
            ctx.tg.send_message(
                chat_id,
                '<b>ℹ️ Информация</b>\n\n'
                + (caption or 'Нет данных для отображения'),
                options)
            return None

        # Несколько файлов photo/video/audio/document (и media_group)
        # → sendMediaGroup: Telegram принимает альбом из 2–10 элементов.
        if media_type in ('photo', 'video', 'audio', 'document',
                          'media_group') and total_files > 1:
            # У однотипных медиа тип элемента = тип шага; у media_group
            # определяем по расширению URL, file_id уходит document.
            plain_media = ('photo', 'video', 'audio', 'document')
            all_media: list[dict] = []
            for url in urls:
                item_type = media_type if media_type in plain_media \
                    else _guess_url_media_type(url)
                media_item: dict[str, Any] = {'type': item_type, 'media': url}
                if not all_media and caption and caption.strip() != '':
                    media_item['caption'] = caption.strip()
                    media_item['parse_mode'] = 'HTML'
                all_media.append(media_item)
            for file in file_ids:
                item_type = media_type if media_type in plain_media \
                    else 'document'
                media_item: dict[str, Any] = {'type': item_type, 'media': file}
                if not all_media and caption and caption.strip() != '':
                    media_item['caption'] = caption.strip()
                    media_item['parse_mode'] = 'HTML'
                all_media.append(media_item)

            if len(all_media) > 1:
                return send_media_group_universal(ctx, chat_id, all_media,
                                                  caption, options)

        # Один файл
        if urls:
            media_source = urls[0]
        elif file_ids:
            media_source = file_ids[0]
        else:
            ctx.tg.send_message(
                chat_id,
                '<b>ℹ️ Информация</b>\n\n'
                + (caption or 'Нет данных для отображения'),
                options)
            return None

        payload: dict[str, Any] = {'chat_id': chat_id}
        payload[_media_payload_field(media_type)] = media_source
        if media_type in CAPTION_MEDIA_TYPES \
                and caption and str(caption).strip() != '':
            payload['caption'] = str(caption).strip()
            payload['parse_mode'] = 'HTML'
        if options.get('reply_markup'):
            payload['reply_markup'] = options['reply_markup']

        method = _MEDIA_METHOD_MAP.get(media_type, 'sendMessage')
        response = ctx.tg.send_request(method, payload)
        if response.get('ok'):
            return response['result']['message_id']

        description = str(response.get('description') or '')
        if media_type != 'document' and any(
                marker in description
                for marker in ('wrong file identifier', 'file_id', 'Wrong character')):
            document_payload: dict[str, Any] = {
                'chat_id': chat_id,
                'document': media_source,
                'caption': caption or 'Файл',
                'parse_mode': 'HTML',
            }
            if options.get('reply_markup'):
                document_payload['reply_markup'] = options['reply_markup']
            doc_response = ctx.tg.send_request('sendDocument', document_payload)
            if doc_response.get('ok'):
                return doc_response['result']['message_id']

        ctx.tg.send_message(
            chat_id,
            '<b>ℹ️ Информация</b>\n\n'
            + (caption or 'Не удалось отправить медиафайл'),
            options)
        return None
    except Exception as error:  # noqa: BLE001
        logger.error('Error in sendMediaMessage: %s', error)
        caption = step.text
        if caption and str(caption).strip() != '':
            ctx.tg.send_message(chat_id,
                                '<b>ℹ️ Информация</b>\n\n' + str(caption),
                                options)
        return None


# ---------------------------------------------------------------------------
# Barcode / Poll / Quiz / Dice отправка
# ---------------------------------------------------------------------------

def send_barcode_message(ctx: BotContext, chat_id, step: Step,
                         options: dict, user_id: Any) -> Any:
    """Порт sendBarcodeMessage (:4439)."""
    try:
        time.sleep(0.5)

        barcode_info = parse_barcode_data(ctx, step, user_id)

        if user_id:
            users.save_user_input_response(
                ctx, user_id, step.type_value or 'Barcode',
                barcode_info['data'])

        qr_code_url = generate_barcode_url(barcode_info['data'],
                                           barcode_info['options'],
                                           barcode_info['qrConfig'])
        if not qr_code_url:
            ctx.tg.send_message(
                chat_id,
                '<b>❌ Ошибка генерации QR-кода</b>\n\n'
                'Не удалось сгенерировать код для данных: '
                + str(barcode_info['data'])[:50] + '...',
                options)
            return None

        caption = step.text
        processed_caption = ''
        if caption and str(caption).strip() != '':
            processed_caption = (users.replace_tags_in_text(ctx, caption, user_id)
                                 if user_id else caption)

        try:
            message_id = ctx.tg.send_photo(chat_id, qr_code_url,
                                           processed_caption, options)
            if message_id:
                if step.pin:
                    ctx.tg.pin_message(chat_id, message_id)
                return message_id
        except Exception as error:  # noqa: BLE001
            logger.error('Error sending barcode: %s', error)
            base64_image = download_image_as_base64(qr_code_url)
            if base64_image:
                return ctx.tg.send_photo(chat_id, base64_image,
                                         processed_caption, options)
            ctx.tg.send_message(
                chat_id,
                '<b>🎫 Ваш QR-код сгенерирован!</b>\n\n'
                f'Данные: <code>{barcode_info["data"]}</code>\n\n'
                f'Ссылка на код: {qr_code_url}',
                options)

        return None
    except Exception as error:  # noqa: BLE001
        logger.error('Error in sendBarcodeMessage: %s', error)
        ctx.tg.send_message(
            chat_id,
            '<b>❌ Ошибка при создании QR-кода</b>\n\n'
            'Попробуйте позже или обратитесь к администратору.',
            options)
        return None


def handle_poll_message(ctx: BotContext, chat_id, step: Step, options: dict,
                        user_id: Any) -> None:
    """Порт MessageHandler.handlePollMessage (:5733)."""
    poll = step.poll
    if not poll or not 2 <= len(poll.answers) <= 10:
        ctx.tg.send_message(
            chat_id,
            '<b>❌ Ошибка:</b> Для опроса нужно от 2 до 10 вариантов ответа. '
            'Проверьте поле с вариантами.')
        return

    question = poll.question
    if user_id:
        question = users.replace_tags_in_text(ctx, question, user_id)

    poll_options: dict[str, Any] = {
        'type': 'regular',
        'is_anonymous': poll.anonymous,
        'allows_multiple_answers': poll.multiple,
    }
    if poll.close_date is not None:
        poll_options['close_date'] = poll.close_date
    elif poll.open_period is not None:
        poll_options['open_period'] = poll.open_period
    if options.get('reply_markup'):
        poll_options['reply_markup'] = options['reply_markup']

    poll_result = ctx.tg.send_poll(chat_id, question, poll.answers,
                                   poll_options)
    if poll_result and poll_result.get('message_id') and poll_result.get('poll_id'):
        add_poll_result(ctx, poll_result['poll_id'], user_id, chat_id, step)
        if step.pin:
            ctx.tg.pin_message(chat_id, poll_result['message_id'])


def handle_quiz_message(ctx: BotContext, chat_id, step: Step, options: dict,
                        user_id: Any) -> None:
    """Порт MessageHandler.handleQuizMessage (:5781)."""
    quiz = step.quiz
    if not quiz or not 2 <= len(quiz.answers) <= 10:
        ctx.tg.send_message(
            chat_id,
            '<b>❌ Ошибка:</b> Для викторины нужно от 2 до 10 вариантов '
            'ответа. Проверьте поле с вариантами.')
        return

    if not (0 <= quiz.correct_option < len(quiz.answers)):
        ctx.tg.send_message(
            chat_id,
            '<b>❌ Ошибка:</b> Неправильный номер правильного ответа. '
            'Проверьте колонку Quiz.')
        return

    question = quiz.question
    if user_id:
        question = users.replace_tags_in_text(ctx, question, user_id)

    poll_options: dict[str, Any] = {
        'type': 'quiz',
        'correct_option_id': quiz.correct_option,
        'is_anonymous': quiz.anonymous,
    }
    if quiz.close_date is not None:
        poll_options['close_date'] = quiz.close_date
    elif quiz.open_period is not None:
        poll_options['open_period'] = quiz.open_period
    if quiz.explanation and quiz.explanation.strip() != '':
        poll_options['explanation'] = quiz.explanation
    if options.get('reply_markup'):
        poll_options['reply_markup'] = options['reply_markup']

    poll_result = ctx.tg.send_poll(chat_id, question, quiz.answers,
                                   poll_options)
    if poll_result and poll_result.get('message_id') and poll_result.get('poll_id'):
        add_poll_result(ctx, poll_result['poll_id'], user_id, chat_id, step)
        if step.pin:
            ctx.tg.pin_message(chat_id, poll_result['message_id'])


def handle_dice_message(ctx: BotContext, chat_id, step: Step, options: dict,
                        user_id: Any) -> None:
    """Порт MessageHandler.handleDiceMessage (:5842)."""
    dice_emoji = step.dice.emoji
    # sendDice не принимает parse_mode/caption — из options шлём только
    # клавиатуру (reply_markup).
    dice_options: dict[str, Any] = {}
    if options.get('reply_markup'):
        dice_options['reply_markup'] = options['reply_markup']
    dice_result = ctx.tg.send_dice(chat_id, dice_emoji, dice_options)

    if dice_result and dice_result.get('message_id'):
        message_id = dice_result['message_id']
        if dice_result.get('dice_value'):
            add_dice_result(ctx, message_id, user_id, chat_id,
                            dice_result['dice_value'], step)
            # Ждём окончания анимации броска — переход (следующий шаг
            # внутри process_dice_result) не должен приходить во время неё.
            time.sleep(dice_animation_seconds(dice_emoji))
            process_dice_result(ctx, message_id)
        else:
            add_dice_result(ctx, message_id, user_id, chat_id, 0, step)
            timer = threading.Timer(dice_animation_seconds(dice_emoji),
                                    check_dice_result,
                                    args=(ctx, message_id, chat_id))
            timer.daemon = True
            timer.start()

        if step.pin:
            ctx.tg.pin_message(chat_id, message_id)


# ---------------------------------------------------------------------------
# sendStepMessage / updateStepMessage / completeFunnel
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Адвент-календарь: клавиатура и текст шага (строятся при отправке —
# в самом шаге не хранятся) + открытие двери по callback adv_<N>.
# ---------------------------------------------------------------------------

ADVENT_BUTTONS_PER_ROW = 8


def _advent_opened_set(ctx: BotContext, days: int, user_id: Any) -> set[int]:
    """Номера уже открытых дверей: тег advent_opened_<N> == '1'."""
    if not user_id:
        return set()
    tags = users.get_all_user_tags(ctx, user_id)
    return {number for number in range(1, days + 1)
            if str(tags.get(f'advent_opened_{number}') or '') == '1'}


def build_advent_keyboard(ctx: BotContext, step: Step,
                          user_id: Any) -> dict:
    """Клавиатура адвента: ряды по 8 дверей, callback = adv_<N>.

    Эмодзи двери: ✅ — открыта; 🎁 — не открыта, но день наступил (1..day_index);
    🔒 — будущая.
    """
    adv = step.advent
    if not adv:
        return {'inline_keyboard': []}
    day_index = advent_current_day(adv.start, adv.days, adv.tz)
    opened = _advent_opened_set(ctx, adv.days, user_id)

    rows: list[list[dict]] = []
    row: list[dict] = []
    for number in range(1, adv.days + 1):
        if number in opened:
            icon = '✅'
        elif 1 <= number <= day_index:
            icon = '🎁'
        else:
            icon = '🔒'
        row.append({'text': f'{icon} {number}',
                    'callback_data': f'adv_{number}'})
        if len(row) == ADVENT_BUTTONS_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return {'inline_keyboard': rows}


def advent_text(ctx: BotContext, step: Step, text: Any,
                user_id: Any) -> str:
    """Текст шага адвента: {advent_day}/{advent_opened}/{advent_total} + теги юзера."""
    raw = str(text or '')
    adv = step.advent
    if adv:
        # Подставляем до replace_tags_in_text, чтобы плейсхолдеры не
        # уходили в condition-резолвер.
        day_index = advent_current_day(adv.start, adv.days, adv.tz)
        opened = len(_advent_opened_set(ctx, adv.days, user_id))
        raw = (raw.replace('{advent_day}', str(day_index))
                  .replace('{advent_opened}', str(opened))
                  .replace('{advent_total}', str(adv.days)))
    result = users.replace_tags_in_text(ctx, raw, user_id)
    return result if isinstance(result, str) else str(result or '')


def open_advent_door(ctx: BotContext, step: Step, user_id: Any,
                     day: int) -> dict:
    """Открытие двери адвента: тег открытой двери + приз-монеты дня.

    Возвращает {'popup': str, 'opened': bool}: popup — текст всплывающего
    ответа (обрезка до 200 символов — у вызывающего); opened=True — дверь
    открыта этим вызовом (клавиатуру нужно перерисовать).
    """
    adv = step.advent
    if not adv or day < 1:
        return {'popup': '', 'opened': False}

    day_index = advent_current_day(adv.start, adv.days, adv.tz)
    row = ctx.storage.get_user(ctx.funnel_id, user_id)
    data: dict = dict(row.get('data') or {}) if row else {}
    tag = f'advent_opened_{day}'

    if str(data.get(tag) or '') == '1':
        return {'popup': adv.done, 'opened': False}
    if day > day_index:
        return {'popup': adv.early.replace('{day}', str(day)),
                'opened': False}

    # Открываем дверь: тег навсегда + приз дня (индекс day-1 в advent_prizes).
    data[tag] = '1'
    popup = f'🎁 Дверь {day} открыта!'
    if day <= len(adv.prizes):
        title, amount = split_advent_prize(adv.prizes[day - 1])
        if title:
            popup = title
        if amount is not None:
            # Начисление в тег баланса — как в execute_purchase.
            balance = functions._purchase_number(
                data.get(config.PURCHASE_TAG))
            data[config.PURCHASE_TAG] = functions._purchase_normalize(
                balance + amount)

    ctx.storage.save_user_row(ctx.funnel_id, user_id,
                              users.get_user_state(ctx, user_id), data)
    return {'popup': popup, 'opened': True}


_TYPING_ACTION_MAP = {
    'photo': 'upload_photo',
    'video': 'upload_video',
    'audio': 'upload_audio',
    'document': 'upload_document',
    'voice': 'upload_audio',
    'video_note': 'upload_video_note',
    'animation': 'upload_video',
    'sticker': 'choose_sticker',
    'media_group': 'upload_photo',
    'poll': 'typing',
    'quiz': 'typing',
    'dice': 'choose_sticker',
    'barcode': 'upload_photo',
    'text': 'typing',
    'input': 'typing',
}


def _pin_if_needed(ctx: BotContext, chat_id, message_id: Any,
                   should_pin: bool) -> None:
    if message_id and should_pin:
        ctx.tg.pin_message(chat_id, message_id)


def _url_auto_fire(ctx: BotContext, chat_id, step_number: int,
                   user_id: Any, target: int) -> None:
    """Таймер url-кнопки: переход, если пользователь всё ещё на шаге."""
    try:
        if users.get_user_state(ctx, user_id) != step_number:
            return
        users.set_user_state(ctx, user_id, target)
        send_step_message(ctx, chat_id, target, user_id)
    except Exception as error:  # noqa: BLE001
        logger.error('[url_auto_fire] %s', error)


def _schedule_url_auto(ctx: BotContext, chat_id, step_number: int,
                       user_id: Any, step) -> None:
    """URL-кнопки: эмуляция авто-перехода с задержкой.

    Telegram не сообщает боту о клике по url-кнопке, поэтому переход
    планируется от показа шага: через delay секунд, если пользователь
    не ушёл дальше (state не изменился).
    """
    if not user_id or not step.inline_keyboard:
        return
    parsed = step.inline_keyboard.get('parsedData') or {}
    for row in parsed.get('buttons') or []:
        for btn in row:
            if (btn.get('type') or '') != 'url':
                continue
            try:
                target = int(btn.get('step') or 0)
                delay = int(btn.get('delay') or 0)
            except (TypeError, ValueError):
                continue
            if target <= 0 or delay <= 0 or target == step_number:
                continue
            timer = threading.Timer(
                delay, _url_auto_fire,
                args=(ctx, chat_id, step_number, user_id, target))
            timer.daemon = True
            timer.start()


def send_step_message(ctx: BotContext, chat_id, step_number: int,
                      user_id: Any = None) -> None:
    """Порт MessageHandler.sendStepMessage (:5503) — диспетчер типов шагов."""
    step = StepManager.get_step_data(ctx, step_number)
    if not step:
        ctx.tg.send_message(
            chat_id, f'<b>⚠️ Шаг {step_number} не найден в воронке</b>')
        return

    options: dict[str, Any] = {'parse_mode': 'HTML'}

    user_role = (users.get_user_tag(ctx, user_id, 'role') or '') if user_id else ''
    reply_keyboard = build_reply_keyboard(ctx, step_number, user_role)
    if reply_keyboard:
        options['reply_markup'] = reply_keyboard['reply_markup']
    elif step.inline_keyboard and step.inline_keyboard.get('inlineKeyboard'):
        options['reply_markup'] = step.inline_keyboard['inlineKeyboard']

    # Адвент: клавиатура строится при отправке и перекрывает клавиатуру шага.
    if step.type == 'advent':
        options['reply_markup'] = build_advent_keyboard(ctx, step, user_id)

    has_effects = effects.has_effects(step.text)

    processed_text = step.text
    if step.type == 'advent':
        processed_text = advent_text(ctx, step, step.text, user_id)
        # Без текста сообщение не отправится — двери календаря потерялись.
        if not str(processed_text or '').strip():
            processed_text = '🎁 Адвент-календарь'
    elif user_id:
        processed_text = users.replace_tags_in_text(ctx, step.text, user_id)

    if ctx.config.get('typing', False):
        action = _TYPING_ACTION_MAP.get(step.type, 'typing')
        ctx.tg.send_chat_action(chat_id, action)

    if step.type == 'barcode':
        send_barcode_message(ctx, chat_id, step, options, user_id)
    elif step.type == 'poll':
        handle_poll_message(ctx, chat_id, step, options, user_id)
    elif step.type == 'quiz':
        handle_quiz_message(ctx, chat_id, step, options, user_id)
    elif step.type == 'dice':
        handle_dice_message(ctx, chat_id, step, options, user_id)
    elif step.type in config.MEDIA_TYPES:
        send_media_message(ctx, chat_id, step, step.type, options,
                           caption=processed_text)
    else:
        message_text = processed_text or ''
        if message_text.strip() != '':
            if not has_effects:
                message_id = ctx.tg.send_message(chat_id, message_text, options)
                _pin_if_needed(ctx, chat_id, message_id, step.pin)
            else:
                try:
                    initial_text = effects.get_initial_text(message_text)
                    initial_message_id = ctx.tg.send_message(
                        chat_id, initial_text if initial_text else ' ', options)

                    if initial_message_id:
                        time.sleep(0.5)
                        try:
                            effects_processed = effects.process_effects(
                                ctx.tg, chat_id, initial_message_id,
                                message_text, user_id,
                                edit_options=options)
                            if effects_processed:
                                _pin_if_needed(ctx, chat_id, initial_message_id,
                                               step.pin)
                        except Exception:  # noqa: BLE001
                            final_text = effects.get_final_text(message_text)
                            if final_text and final_text.strip() != '':
                                ctx.tg.edit_message_text(chat_id, initial_message_id,
                                                         final_text, options)
                except Exception:  # noqa: BLE001
                    ctx.tg.send_message(chat_id,
                                        effects.get_final_text(message_text),
                                        options)

    # url-кнопки: авто-переход через задержку от показа шага.
    _schedule_url_auto(ctx, chat_id, step_number, user_id, step)

    # Функции с trigger=step — после показа шага, до авто-перехода.
    # Без user_id (превью/авто) нет тегов юзера — пропускаем.
    trigger_branch = 0
    if user_id and step_number > 0:
        trig_msgs, trigger_branch = functions.run_functions_for_step(
            ctx, user_id, step_number)
        for msg in trig_msgs:
            ctx.tg.send_message(chat_id, msg)

    # Ветка condition с триггером «шаг» перекрывает авто-next.
    if user_id and trigger_branch > 0 \
            and step.type not in ('dice', 'poll', 'quiz'):
        if trigger_branch != step_number:
            users.set_user_state(ctx, user_id, trigger_branch)
            send_step_message(ctx, chat_id, trigger_branch, user_id)
        return

    # Автопереход только при scroll=auto И явно заданном next_step > 0.
    # Без auto последовательного авто-перехода нет; пустой next_step при
    # auto тоже не даёт перехода (без фолбэка current+1).
    # dice/poll/quiz переходят сами после броска/ответа (process_dice_result /
    # process_poll_result уже отправляют целевой шаг: маппинг, next_step или
    # конец воронки) — авто-блок здесь срабатывал бы до ответа и дублировал
    # публикацию шага.
    if user_id and step.auto and step.type not in ('dice', 'poll', 'quiz'):
        if step.delay > 0:
            time.sleep(step.delay)

        next_step = step.next if step.next is not None and step.next > 0 else 0

        if next_step > 0:
            users.set_user_state(ctx, user_id, next_step)
            send_step_message(ctx, chat_id, next_step, user_id)
        elif step.next == 0:
            # Явный '0' при auto — конец воронки (как в старом формате).
            complete_funnel(ctx, user_id, chat_id)


def update_step_message(ctx: BotContext, chat_id, message_id: int,
                        step_number: int, user_id: Any = None) -> None:
    """Порт MessageHandler.updateStepMessage (:5664): editMessageText."""
    step = StepManager.get_step_data(ctx, step_number)
    if not step:
        ctx.tg.send_message(
            chat_id, f'<b>⚠️ Шаг {step_number} не найден в воронке</b>')
        return

    options: dict[str, Any] = {'parse_mode': 'HTML'}
    user_role = (users.get_user_tag(ctx, user_id, 'role') or '') if user_id else ''
    reply_keyboard = build_reply_keyboard(ctx, step_number, user_role)
    if reply_keyboard:
        options['reply_markup'] = reply_keyboard['reply_markup']
    elif step.inline_keyboard and step.inline_keyboard.get('inlineKeyboard'):
        options['reply_markup'] = step.inline_keyboard['inlineKeyboard']

    # Адвент: клавиатура строится заново (обновить ✅ на открытых дверях).
    if step.type == 'advent':
        options['reply_markup'] = build_advent_keyboard(ctx, step, user_id)

    has_effects = effects.has_effects(step.text)
    processed_text = step.text
    if step.type == 'advent':
        processed_text = advent_text(ctx, step, step.text, user_id)
        # Без текста сообщение не отправится — двери календаря потерялись.
        if not str(processed_text or '').strip():
            processed_text = '🎁 Адвент-календарь'
    elif user_id:
        processed_text = users.replace_tags_in_text(ctx, step.text, user_id)

    if step.type in ('barcode', 'poll', 'quiz', 'dice') \
            or step.type in config.MEDIA_TYPES:
        ctx.tg.delete_message(chat_id, message_id)
        send_step_message(ctx, chat_id, step_number, user_id)
        return

    message_text = processed_text or ''
    if message_text.strip() == '':
        return

    if not has_effects:
        ctx.tg.edit_message_text(chat_id, message_id, message_text, options)
        _schedule_url_auto(ctx, chat_id, step_number, user_id, step)
        return

    try:
        initial_text = effects.get_initial_text(message_text)
        if initial_text:
            ctx.tg.edit_message_text(chat_id, message_id, initial_text, options)
        time.sleep(0.5)
        try:
            effects.process_effects(ctx.tg, chat_id, message_id, message_text,
                                    user_id, edit_options=options)
        except Exception:  # noqa: BLE001
            final_text = effects.get_final_text(message_text)
            if final_text and final_text.strip() != '':
                ctx.tg.edit_message_text(chat_id, message_id, final_text, options)
    except Exception:  # noqa: BLE001
        ctx.tg.edit_message_text(chat_id, message_id,
                                 effects.get_final_text(message_text), options)
    _schedule_url_auto(ctx, chat_id, step_number, user_id, step)


def complete_funnel(ctx: BotContext, user_id: Any, chat_id) -> None:
    """Порт MessageHandler.completeFunnel (:6218): финальное сообщение + шаг 0."""
    final_message = functions.get_system_final_message(ctx)
    if final_message and ctx.config.get('systemmessages', True):
        processed = users.replace_tags_in_text(ctx, final_message, user_id)
        ctx.tg.send_message(chat_id, f'<b>{processed}</b>')

    users.set_user_state(ctx, user_id, 0)



