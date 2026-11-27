"""카카오 전용 입출력 서버. 실행: python -m uvicorn app.kakao:app --port 8001"""
import logging
import os
import secrets
import threading
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app import intake_store as store  # .env를 먼저 읽는다.
from agents.lost_intake import button, handle_message, reply

logger = logging.getLogger(__name__)


class KakaoUser(BaseModel):
    id: str = Field(min_length=1, max_length=100)


class UserRequest(BaseModel):
    user: KakaoUser
    utterance: str = Field(min_length=1, max_length=2000)
    callbackUrl: str | None = Field(default=None, max_length=2000)


class KakaoAction(BaseModel):
    clientExtra: dict | None = None


class SkillRequest(BaseModel):
    bot: KakaoUser
    userRequest: UserRequest
    action: KakaoAction = Field(default_factory=KakaoAction)


def kakao_response(answer):
    """우리 답변을 카카오 규격(version/template/simpleText)에 맞춰 포장한다."""
    text = answer["text"]
    outputs = [{"simpleText": {"text": text[i:i + 1000]}} for i in range(0, min(len(text), 3000), 1000)]
    quick_replies = [{"label": b["label"], "action": "message", "messageText": b["label"],
                      "extra": b.get("extra", {})} for b in answer.get("buttons", [])]
    return {"version": "2.0", "template": {"outputs": outputs, "quickReplies": quick_replies}}


def check_skill_key(x_skill_key: str = Header(default="")):
    # 스킬 설정의 '헤더값 입력'에 X-Skill-Key를 등록한다. 키는 Git에 올리지 않는다.
    expected = os.environ["KAKAO_SKILL_SECRET"]
    if not secrets.compare_digest(x_skill_key.encode(), expected.encode()):
        raise HTTPException(401, "스킬 인증 키가 일치하지 않습니다.")


def validate_callback(url):
    """카카오 콜백만 허용한다. 외부에서 받은 임의 주소로 서버가 요청하지 않는다."""
    if not url:
        return
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        valid = (parsed.scheme == "https" and host.endswith(".kakao.com")
                 and parsed.port in (None, 443) and not parsed.username
                 and not parsed.password and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(422, "카카오 HTTPS 콜백 주소가 필요합니다.")


def deliver_callback(event, answer):
    if not event.get("callback_url"):
        return
    # 콜백은 1회용이므로 자동 재전송하지 않는다. 실패 시 '결과 확인'으로 DB 결과를 읽는다.
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.post(event["callback_url"], json=kakao_response(answer))
            response.raise_for_status()
            body = response.json()
            status = "sent" if body.get("status") == "SUCCESS" else "failed"
    except (httpx.HTTPError, ValueError):
        status = "failed"
    store.record_callback(event["event_id"], status)


def process_event(event_id):
    """HTTP 응답을 먼저 보낸 뒤 실행된다. AI 처리 때문에 카카오 요청을 붙잡지 않는다."""
    event = None
    try:
        event, state = store.begin(event_id)
        turn = handle_message(state, event["message"], event["extra"])
        answer = store.complete(event, turn)
    except Exception as error:
        # 예외 문자열에는 API 주소/키 등이 들어갈 수 있어 종류만 기록한다.
        logger.warning("접수 요청 %s 처리 실패: %s", event_id, type(error).__name__)
        answer = reply("처리 중 문제가 생겼습니다. 이전 입력은 유지됩니다. 같은 내용을 다시 보내주세요.")
        try:
            store.fail(event_id, answer)
        except Exception as db_error:
            logger.warning("오류 상태 저장 실패: %s", type(db_error).__name__)
    finally:
        app.state.slots.release()
    if event:
        try:
            deliver_callback(event, answer)
        except Exception as error:
            logger.warning("콜백 상태 기록 실패: %s", type(error).__name__)


@asynccontextmanager
async def lifespan(app):
    if not os.getenv("KAKAO_SKILL_SECRET"):
        raise RuntimeError(".env에 KAKAO_SKILL_SECRET을 설정하세요.")
    store.initialize()
    with store.server_lock():
        store.recover()
        app.state.slots = threading.BoundedSemaphore(20)
        yield


app = FastAPI(title="분실물 접수", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/")
@app.get("/health")
def health():
    with store.connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "service": "kakao-lost-intake", "intake_api_ready": True}


@app.post("/kakao/skill", dependencies=[Depends(check_skill_key)])
def receive_message(payload: SkillRequest, background: BackgroundTasks):
    """읽는 순서 ①: 카카오 메시지가 들어오는 입구. 사용자와 문장을 꺼낸다."""
    bot_id = payload.bot.id
    user_id = payload.userRequest.user.id
    message = payload.userRequest.utterance.strip()
    callback_url = payload.userRequest.callbackUrl
    expected_bot = os.getenv("KAKAO_BOT_ID")
    if expected_bot and bot_id != expected_bot:
        raise HTTPException(403, "등록된 봇이 아닙니다.")
    if not message:
        raise HTTPException(422, "메시지가 비어 있습니다.")
    validate_callback(callback_url)

    # 이 명령은 AI를 호출하지 않고 이미 만들어진 답변을 바로 보여준다.
    if message == "결과 확인":
        session = store.latest_reply(bot_id, user_id)
        if session and session["pending_event"]:
            answer = reply("아직 처리 중입니다. 잠시 후 다시 확인해주세요.", button("결과 확인"))
        else:
            answer = (session or {}).get("last_reply") or reply("분실한 물건을 말씀해주세요.", button("분실물 접수"))
        return kakao_response(answer)

    if not app.state.slots.acquire(blocking=False):
        return kakao_response(reply("접수 요청이 많습니다. 잠시 후 같은 내용을 다시 보내주세요."))
    try:
        result, event_id = store.enqueue(bot_id, user_id, message,
                                         payload.action.clientExtra or {}, callback_url)
    except Exception:
        app.state.slots.release()
        raise
    if result != "accepted":
        app.state.slots.release()
        if result == "duplicate":
            return {"version": "2.0", "useCallback": True}
        return kakao_response(reply("이전 메시지를 처리 중입니다. 결과를 확인한 뒤 새 내용을 다시 보내주세요.", button("결과 확인")))

    background.add_task(process_event, event_id)
    if callback_url:
        return {"version": "2.0", "useCallback": True}
    return kakao_response(reply("내용을 확인하고 있습니다. 잠시 후 아래 버튼을 눌러주세요.", button("결과 확인")))
