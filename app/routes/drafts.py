"""습득물 공고 초안 API: 등록(대기열) → 상태 조회 → 담당자 결정."""
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from langgraph.types import Command
from pydantic import BaseModel

from agents.categories import CATEGORIES, CHECKS
from app import jobs

router = APIRouter(prefix="/api")
UPLOADS = Path("uploads")
PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}   # OpenAI 이미지 입력이 받는 형식
MAX_PHOTO = 10 * 1024 * 1024


@router.get("/categories")
def categories():
    return {"categories": CATEGORIES, "checks": CHECKS}


@router.post("/drafts")
def create_draft(date: str = Form(...), place: str = Form(...), finder_name: str = Form(...),
                 category_main: str = Form(""), category_sub: str = Form(""),
                 memo: str = Form(""), checks: list[str] = Form([]),
                 photo: UploadFile | None = File(None)):
    """초안 요청 → 바로 thread_id만 돌려주고, 처리는 뒤에서 한다."""
    if jobs.is_full():
        raise HTTPException(429, "대기 중인 작업이 많습니다. 잠시 후 다시 시도해 주세요.")
    if category_main and category_main not in CATEGORIES:        # 화면에서 온 값 검사
        raise HTTPException(422, f"없는 대분류: {category_main}")
    if category_sub and category_sub not in CATEGORIES.get(category_main, []):
        raise HTTPException(422, f"없는 소분류: {category_sub}")
    checks = [c for c in checks if c in CHECKS]

    thread_id = uuid.uuid4().hex                  # 실행마다 새 thread_id
    photo_path = None
    if photo and photo.filename:
        suffix = Path(photo.filename).suffix.lower()
        if suffix not in PHOTO_TYPES:             # 모델에 그대로 보내므로 이미지 형식만 받음
            raise HTTPException(422, "사진은 jpg, png, webp, gif만 올릴 수 있습니다.")
        data = photo.file.read(MAX_PHOTO + 1) # !! 메모리 처리 1 바이트 더 읽게 하는 멋진 아이디어 !!
        if len(data) > MAX_PHOTO:
            raise HTTPException(413, "사진은 10MB까지 올릴 수 있습니다.")
        UPLOADS.mkdir(exist_ok=True)
        photo_path = str(UPLOADS / f"{thread_id}{suffix}")
        Path(photo_path).write_bytes(data) # 담당자 두명이 같은 이미지로 쓰면 덮어써지지만 접수번호로 저장하면 안겹침
    report = {"date": date, "place": place, "finder_name": finder_name, "category_main": category_main,
              "category_sub": category_sub, "memo": memo, "checks": checks}
    jobs.submit(thread_id, {"report": report, "photo_path": photo_path, "handoffs": []}, # 1. 전엔 invoke로 직접실행했는데 이제 대기열에 넣기만 하고 초안이 금방 나옴
                title=memo[:30] or category_main or "습득물", place=place, date=date)
    return {"thread_id": thread_id, "status": "queued"} # 2. 그래프 끝날때 까지 안기다리게 할 수 있게 접수번호만 바로 돌려준다.


@router.get("/drafts")
def list_drafts():
    return sorted(jobs.JOBS.values(), key=lambda j: j["created"], reverse=True) # 작업을 만든 시각 기준 최신순으로 돌려줌


@router.get("/drafts/{thread_id}") # thread_id 없으면 오류
def get_draft(thread_id: str):
    if thread_id not in jobs.JOBS:
        raise HTTPException(404, "없는 작업입니다.")
    return jobs.JOBS[thread_id]


class Decision(BaseModel):
    action: Literal["승인", "수정후승인", "수정요청", "폐기"]
    public: dict[str, str] = {}                   # 수정후승인: 담당자가 정한 공개 특징
    verify: dict[str, str] = {}                   # 수정후승인: 담당자가 정한 본인확인용 특징
    item_name: str = ""
    comment: str = ""                             # 수정요청: 에이전트에게 줄 코멘트


@router.post("/drafts/{thread_id}/decision")
def decide(thread_id: str, d: Decision):
    job = jobs.JOBS.get(thread_id)
    if not job or job["status"] != "waiting_approval":           # 두 번 누르기 방지
        raise HTTPException(409, "승인 대기 중인 작업이 아닙니다.")
    if d.action == "수정요청" and not d.comment.strip(): # 수정 코멘트 없으면 에이전트가 뭘 고칠지 몰라서 같은 결과 만듦
        raise HTTPException(422, "수정 요청에는 코멘트가 필요합니다.")
    jobs.submit(thread_id, Command(resume=d.model_dump())) # 위에서 받은 json으로 결정
    return {"thread_id": thread_id, "status": "queued"}
