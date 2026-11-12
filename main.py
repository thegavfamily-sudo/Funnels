# ============================================================================
# main.py — точка входа Python-бота
#
# - один поток (threading.Thread) на каждый активный бот (токен);
# - getUpdates long polling с offset, timeout=30;
# - реестр ботов и кэши funnels/steps/functions перечитываются из Supabase
#   каждые SYNC_INTERVAL_SECONDS (config.py);
# - маршрутизация апдейтов — порт doPost из apps_script/БОТ_готовый.gs:5359
#   (message / callback_query / poll_answer; inline_query не нужен).
#
# Запуск:  python main.py
# ============================================================================

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import config
import handlers
import steps
from storage import BotContext, Storage
from supabase_client import SupabaseClient
from telegram_api import TelegramAPI

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger('main')


# ---------------------------------------------------------------------------
# Маршрутизация апдейтов (doPost :5359)
# ---------------------------------------------------------------------------

def dispatch_poll_answer(ctx: BotContext, poll_answer: dict) -> None:
    """Порт обработки update.poll_answer (:5429): poll → quiz → fallback."""
    poll_id = poll_answer.get('poll_id')
    user_id = (poll_answer.get('user') or {}).get('id')
    option_ids = poll_answer.get('option_ids') or []
    if not poll_id or not option_ids:
        return

    selected_option = option_ids[0]
    storage = ctx.storage

    if poll_id in storage.poll_results:
        storage.poll_results[poll_id]['selectedOption'] = selected_option
        time.sleep(0.5)
        steps.process_poll_result(ctx, poll_id, 'poll')
        return

    if poll_id in storage.quiz_results:
        storage.quiz_results[poll_id]['selectedOption'] = selected_option
        time.sleep(0.5)
        steps.process_poll_result(ctx, poll_id, 'quiz')
        return

    # Фолбэк: поиск необработанного опроса по userId
    for key, value in list(storage.poll_results.items()):
        if value.get('userId') == user_id and not value.get('processed'):
            value['selectedOption'] = selected_option
            time.sleep(0.5)
            steps.process_poll_result(ctx, key, 'poll')
            return
    for key, value in list(storage.quiz_results.items()):
        if value.get('userId') == user_id and not value.get('processed'):
            value['selectedOption'] = selected_option
            time.sleep(0.5)
            steps.process_poll_result(ctx, key, 'quiz')
            return


def dispatch_update(ctx: BotContext, update: dict) -> None:
    if update.get('poll_answer'):
        dispatch_poll_answer(ctx, update['poll_answer'])
        return
    if update.get('message'):
        if update['message'].get('dice'):
            return
        handlers.handle_message(ctx, update['message'])
        return
    if update.get('callback_query'):
        handlers.handle_callback_query(ctx, update['callback_query'])
        return
    # inline_query / edited_message / channel_post не обрабатываются


# ---------------------------------------------------------------------------
# Поток бота (long polling)
# ---------------------------------------------------------------------------

class BotWorker:
    def __init__(self, ctx: BotContext):
        self.ctx = ctx
        self._stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name=f'bot-{ctx.bot_id}', daemon=True)

    def start(self) -> None:
        self.thread.start()
        logger.info('Бот %s запущен (воронка %s)', self.ctx.bot_id,
                    self.ctx.funnel_id)

    def stop(self) -> None:
        self._stop_event.set()

    def join(self, timeout: float = 35.0) -> None:
        self.thread.join(timeout=timeout)

    def _run(self) -> None:
        offset: Optional[int] = None
        while not self._stop_event.is_set():
            try:
                updates = self.ctx.tg.get_updates(
                    offset=offset, timeout=config.POLL_TIMEOUT_SECONDS)
            except Exception as error:  # noqa: BLE001
                logger.error('Бот %s: getUpdates: %s', self.ctx.bot_id, error)
                time.sleep(3)
                continue

            if updates is None:
                # Ошибка запроса (лог внутри send_request); пауза перед повтором
                time.sleep(3)
                continue

            for update in updates:
                offset = update['update_id'] + 1
                if self._stop_event.is_set():
                    break
                try:
                    dispatch_update(self.ctx, update)
                except Exception as error:  # noqa: BLE001
                    logger.error('Бот %s: ошибка обработки апдейта: %s',
                                 self.ctx.bot_id, error)


# ---------------------------------------------------------------------------
# Реестр ботов
# ---------------------------------------------------------------------------

def _spec_key(info: dict) -> tuple:
    return (info['token'], info['funnel_id'],
            tuple(sorted((info['config'] or {}).items())))


def build_context(storage: Storage, bot_id: str, info: dict) -> BotContext:
    return BotContext(
        bot_id=bot_id,
        token=info['token'],
        funnel_id=info['funnel_id'],
        funnel_name=info.get('funnel_name') or '',
        config=dict(info.get('config') or {}),
        storage=storage,
        tg=TelegramAPI(info['token']),
    )


def sync_bots(storage: Storage, workers: dict[str, BotWorker]) -> None:
    """Запуск новых ботов / остановка исчезнувших или изменившихся."""
    active = storage.active_funnels

    for bot_id in list(workers.keys()):
        info = active.get(bot_id)
        worker = workers[bot_id]
        if info is None or worker.ctx.spec_key() != _spec_key(info):
            worker.stop()
            worker.join()
            del workers[bot_id]
            logger.info('Бот %s остановлен', bot_id)

    for bot_id, info in active.items():
        if bot_id not in workers:
            ctx = build_context(storage, bot_id, info)
            worker = BotWorker(ctx)
            workers[bot_id] = worker
            worker.start()


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> None:
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
        logger.error(
            'Не заданы SUPABASE_URL / SUPABASE_SERVICE_KEY. '
            'Заполни файл .env (см. .env.example) и перезапусти бота.')
        raise SystemExit(1)

    client = SupabaseClient(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
    storage = Storage(client)
    storage.refresh()

    workers: dict[str, BotWorker] = {}
    sync_bots(storage, workers)
    logger.info('Синхронизация каждые %d сек; активных ботов: %d',
                config.SYNC_INTERVAL_SECONDS, len(workers))

    try:
        while True:
            time.sleep(config.SYNC_INTERVAL_SECONDS)
            storage.refresh()
            sync_bots(storage, workers)
    except KeyboardInterrupt:
        logger.info('Останавливаю боты...')
    finally:
        for worker in workers.values():
            worker.stop()
        for worker in workers.values():
            worker.join()


if __name__ == '__main__':
    main()
