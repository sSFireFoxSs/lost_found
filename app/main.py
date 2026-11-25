"""웹 서버. 실행(프로젝트 루트에서): .venv/bin/uvicorn app.main:app --port 8000  →  http://127.0.0.1:8000"""
import subprocess
import sys
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes import drafts, notices
from mcp_server.client import MCP_URL


def wait_ready(url, seconds=15):
    """서버가 응답할 때까지 0.1초마다 확인한다."""
    for _ in range(seconds * 10):
        try:
            return httpx.get(url)
        except httpx.ConnectError:
            time.sleep(0.1)
    raise TimeoutError(f"{url} 서버가 켜지지 않았습니다.")


@asynccontextmanager
async def lifespan(app):
    proc = subprocess.Popen([sys.executable, "mcp_server/lost_mcp.py"])   # MCP 서버를 같이 켜고 끈다
    wait_ready(MCP_URL)
    yield
    proc.terminate()
    proc.wait()


app = FastAPI(lifespan=lifespan)
app.include_router(drafts.router)
app.include_router(notices.router)
app.mount("/", StaticFiles(directory="static", html=True))   # 화면: index.html, lost.html, lost_new.html (API 뒤에 둠)
