# Funnel Bot (Python)

Telegram-бот воронок на Python: long polling, Supabase (PostgREST) как хранилище.

## Быстрый старт

```bash
cp .env.example .env   # и вписать SUPABASE_URL / SUPABASE_SERVICE_KEY
pip install -r requirements.txt
python main.py
```

## Важно

- Файл `.env` с ключами **не коммитить** (уже в `.gitignore`).
- В репозиторий попадают только `.env.example` и исходники без секретов.

Подробности — в `ИНСТРУКЦИЯ.txt`.
