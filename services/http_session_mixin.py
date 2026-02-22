"""Shared aiohttp session lifecycle helpers for service classes."""

import aiohttp


class HTTPSessionMixin:
    """Provides lazy aiohttp.ClientSession creation and cleanup."""

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
