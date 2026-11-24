"""웹 화면 서버. 실행: .venv/bin/uvicorn app:app --port 8000  →  http://127.0.0.1:8000"""
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse
from langgraph.types import Command
from pydantic import BaseModel

from graph import graph
from mcp_client import MCP_URL, read_resource

UPLOADS = Path("uploads")


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
    proc = subprocess.Popen([sys.executable, "lost_mcp.py"])   # MCP 서버를 같이 켜고 끈다
    wait_ready(MCP_URL)
    yield
    proc.terminate()
    proc.wait()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.post("/api/draft")
def draft(date: str = Form(...), place: str = Form(...), finder_name: str = Form(...),
          memo: str = Form(""), photo: UploadFile | None = File(None)):
    """신고서·사진을 받아 공고 초안을 만들고, 담당자 승인 직전(interrupt)에서 멈춘다."""
    thread_id = uuid.uuid4().hex                  # 실행마다 새 thread_id
    photo_path = None
    if photo and photo.filename:
        UPLOADS.mkdir(exist_ok=True)
        photo_path = str(UPLOADS / f"{thread_id}{Path(photo.filename).suffix}")
        Path(photo_path).write_bytes(photo.file.read())
    report = {"date": date, "place": place, "finder_name": finder_name, "memo": memo}
    out = graph.invoke({"report": report, "photo_path": photo_path, "handoffs": []},
                       {"configurable": {"thread_id": thread_id}})
    wait = out["__interrupt__"][0].value
    path = ["registration_agent"] + [h["to_agent"] for h in out["handoffs"]]
    return {"thread_id": thread_id, "draft": wait["draft"], "problems": wait["problems"],
            "tries": out.get("tries", 0), "path": path, "item": out["item"]}


class Decision(BaseModel):
    thread_id: str
    decision: str                                 # "승인" / "거절"


@app.post("/api/approve")
def approve(d: Decision):
    state = graph.invoke(Command(resume=d.decision), {"configurable": {"thread_id": d.thread_id}})
    return {"status": state["status"], "notice_id": state.get("notice_id")}


@app.get("/api/notices/{notice_id}")
def notice(notice_id: str):
    return read_resource(f"lost://notices/{notice_id}")
