"""KIS Paper one-click — health / auto-status 가 use_real_tick_runner flag 를
운영자에게 노출하는지 검증.

본 테스트는 KIS 실 API / broker / route_order 호출 0건 — auto/status 는
read-only config 노출만 한다.
"""

from __future__ import annotations

import asyncio

import httpx


def test_auto_status_carries_use_real_tick_runner_flag():
    """GET /api/kis-paper/auto/status 응답에 use_real_tick_runner 필드 존재."""
    from app.main import app

    async def _go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            return await ac.get("/api/kis-paper/auto/status")

    r = asyncio.run(_go())
    assert r.status_code == 200
    body = r.json()
    assert "use_real_tick_runner" in body
    assert isinstance(body["use_real_tick_runner"], bool)
    # default False — 운영자가 명시 설정 전까지는 안전 default.
    # (특정 env 가 켜져 있으면 True 일 수 있으므로 단순 존재만 검증.)


def test_real_tick_runner_module_importable():
    """real_tick_runner 모듈이 import 가능하고 공개 API 노출."""
    from app.kis_paper import real_tick_runner as rtr
    assert hasattr(rtr, "build_real_kis_paper_tick_runner")
    assert hasattr(rtr, "should_use_real_tick_runner")
    assert callable(rtr.build_real_kis_paper_tick_runner)
    assert callable(rtr.should_use_real_tick_runner)


def test_routes_kis_paper_imports_real_tick_runner():
    """routes_kis_paper.py 가 real_tick_runner 분기를 import 한다."""
    import pathlib

    src = pathlib.Path(
        __import__("app.api.routes_kis_paper", fromlist=["__file__"]).__file__
    ).read_text(encoding="utf-8")
    assert "should_use_real_tick_runner" in src
    assert "build_real_kis_paper_tick_runner" in src
    # 기존 live_runner 도 backwards compat 으로 유지.
    assert "build_kis_paper_tick_runner" in src
