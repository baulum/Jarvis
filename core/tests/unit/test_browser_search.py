"""Web search races engines: the first non-empty list wins, failing/blocked engines do not delay it."""

import asyncio

from jarvis.connectors import browser as b


class FakeSvc:
    async def with_page(self, fn):
        return await fn(None)


async def test_race_returns_first_non_empty(monkeypatch) -> None:
    async def slow_google(page, q, n):
        await asyncio.sleep(5)
        return [{"title": "g", "url": "https://g", "snippet": ""}]

    async def blocked_bing(page, q, n):
        await asyncio.sleep(0.05)
        return []  # CAPTCHA / consent wall

    async def ddg(q, n):
        await asyncio.sleep(0.1)
        return [{"title": "d", "url": "https://d", "snippet": ""}]

    monkeypatch.setattr(b, "_google", slow_google)
    monkeypatch.setattr(b, "_bing", blocked_bing)
    monkeypatch.setattr(b, "ddg_http", ddg)
    t = asyncio.get_running_loop().time()
    engine, res = await b.race_search(FakeSvc(), "x", 5, ["bing", "duckduckgo", "google"])  # type: ignore[arg-type]
    assert engine == "duckduckgo" and res[0]["url"] == "https://d"
    assert asyncio.get_running_loop().time() - t < 1


async def test_race_all_fail(monkeypatch) -> None:
    async def boom(*a):
        raise RuntimeError("down")

    monkeypatch.setattr(b, "_bing", boom)
    monkeypatch.setattr(b, "ddg_http", boom)
    assert await b.race_search(FakeSvc(), "x", 5, ["bing", "duckduckgo"]) == ("none", [])  # type: ignore[arg-type]


def test_stale_lock_cleanup(tmp_path) -> None:
    (tmp_path / "SingletonLock").symlink_to("host-123")
    assert b._clear_stale_lock(tmp_path) and not (tmp_path / "SingletonLock").is_symlink()
