# ============================================================================
# supabase_client.py — тонкий PostgREST-клиент для Supabase
#
# Только requests: select (с пагинацией limit/offset — Supabase Max rows=1000),
# upsert (POST + Prefer: resolution=merge-duplicates), patch, delete.
# ============================================================================

from __future__ import annotations

import threading
from typing import Any, Iterable, Optional

import requests

# Supabase по умолчанию отдаёт максимум 1000 строк на запрос.
PAGE_SIZE = 1000


class SupabaseError(Exception):
    """Ошибка запроса к PostgREST (HTTP-статус не 2xx)."""


class SupabaseClient:
    def __init__(self, url: str, service_key: str, timeout: int = 30):
        self.base_url = (url or '').rstrip('/')
        self.timeout = timeout
        self._headers = {
            'apikey': service_key,
            'Authorization': f'Bearer {service_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        self._session = requests.Session()
        self._lock = threading.Lock()

    # -- low level ----------------------------------------------------------

    def _request(self, method: str, path: str, *, params: Optional[dict] = None,
                 json_body: Any = None, headers: Optional[dict] = None) -> Any:
        url = f'{self.base_url}/rest/v1/{path}'
        merged = dict(self._headers)
        if headers:
            merged.update(headers)
        with self._lock:
            resp = self._session.request(
                method, url, params=params, json=json_body,
                headers=merged, timeout=self.timeout,
            )
        if resp.status_code >= 400:
            raise SupabaseError(
                f'{method} {path} -> {resp.status_code}: {resp.text[:500]}'
            )
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # -- select -------------------------------------------------------------

    def select(self, table: str, columns: str = '*',
               filters: Optional[dict[str, Any]] = None,
               order: Optional[str] = None,
               page_size: int = PAGE_SIZE) -> list[dict]:
        """SELECT всех строк таблицы с пагинацией limit/offset.

        filters: {'col': value} → col=eq.value (PostgREST-синтаксис значений
        вида 'eq.x' / 'gt.5' / 'in.(a,b)' передаётся как есть).
        """
        rows: list[dict] = []
        offset = 0
        while True:
            params: dict[str, Any] = {
                'select': columns,
                'limit': page_size,
                'offset': offset,
            }
            if order:
                params['order'] = order
            if filters:
                for key, value in filters.items():
                    params[key] = value
            batch = self._request('GET', table, params=params) or []
            rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += len(batch)
        return rows

    def select_one(self, table: str, columns: str = '*',
                   filters: Optional[dict[str, Any]] = None,
                   order: Optional[str] = None) -> Optional[dict]:
        rows = self.select(table, columns=columns, filters=filters,
                           order=order, page_size=1)
        return rows[0] if rows else None

    # -- write --------------------------------------------------------------

    def upsert(self, table: str, rows: list[dict]) -> None:
        """POST с merge-duplicates: вставка или обновление по PK."""
        if not rows:
            return
        self._request(
            'POST', table, json_body=rows,
            headers={'Prefer': 'resolution=merge-duplicates,return=minimal'},
        )

    def patch(self, table: str, values: dict, filters: dict[str, Any]) -> int:
        """UPDATE по фильтрам; возвращает число затронутых строк (Prefer count)."""
        resp_headers = {'Prefer': 'return=representation'}
        # requests не позволяет считать тело вручную здесь — считаем по репрезентации
        result = self._request('PATCH', table, params=filters, json_body=values,
                               headers=resp_headers)
        if isinstance(result, list):
            return len(result)
        return 0

    def delete(self, table: str, filters: dict[str, Any]) -> int:
        result = self._request('DELETE', table, params=filters,
                               headers={'Prefer': 'return=representation'})
        if isinstance(result, list):
            return len(result)
        return 0
