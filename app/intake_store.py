"""카카오 접수 저장소. 대화 상태·처리 요청·확정된 신고를 PostgreSQL에 저장한다."""
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from agents.lost_schema import LostReport

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")


def connect():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(".env에 DATABASE_URL을 설정하세요.")
    options = conninfo_to_dict(url).get("options", "") + " -c statement_timeout=2000"
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=2, options=options)


def initialize():
    with connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS kakao_intake_sessions (
            bot_id TEXT NOT NULL, user_id TEXT NOT NULL,
            state JSONB NOT NULL DEFAULT '{}', pending_event TEXT,
            last_reply JSONB, PRIMARY KEY (bot_id, user_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS kakao_intake_events (
            event_id TEXT PRIMARY KEY, request_key TEXT UNIQUE,
            bot_id TEXT NOT NULL, user_id TEXT NOT NULL,
            message TEXT NOT NULL, extra JSONB NOT NULL,
            callback_url TEXT, status TEXT NOT NULL DEFAULT 'queued',
            reply JSONB, callback_status TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS lost_reports (
            id BIGSERIAL PRIMARY KEY, draft_id TEXT UNIQUE NOT NULL,
            bot_id TEXT NOT NULL, user_id TEXT NOT NULL,
            fields JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")


@contextmanager
def server_lock():
    # 한 서버만 재시작 복구를 수행한다. 이 개발 서버는 --workers 1로 실행한다.
    with connect() as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(72250602) AS ok").fetchone()["ok"]:
            raise RuntimeError("카카오 접수 서버가 이미 실행 중입니다. --workers 1로 실행하세요.")
        conn.commit()
        yield
        # 연결이 닫히면 세션 잠금도 자동으로 해제된다.


def recover():
    answer = {"text": "서버 재시작으로 마지막 메시지 처리가 중단됐습니다. 같은 내용을 다시 보내주세요.", "buttons": []}
    with connect() as conn:
        conn.execute("UPDATE kakao_intake_events SET status='failed', reply=%s WHERE status IN ('queued','running')",
                     (Jsonb(answer),))
        conn.execute("UPDATE kakao_intake_sessions SET pending_event=NULL, last_reply=%s WHERE pending_event IS NOT NULL",
                     (Jsonb(answer),))


def enqueue(bot_id, user_id, message, extra, callback_url):
    """짧은 트랜잭션으로 한 사용자의 동시 요청/카카오 콜백 재전송을 검사한다."""
    request_key = hashlib.sha256(f"{bot_id}:{user_id}:{callback_url}".encode()).hexdigest() if callback_url else None
    with connect() as conn:
        conn.execute("INSERT INTO kakao_intake_sessions (bot_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                     (bot_id, user_id))
        session = conn.execute("SELECT * FROM kakao_intake_sessions WHERE bot_id=%s AND user_id=%s FOR UPDATE",
                               (bot_id, user_id)).fetchone()
        if request_key and conn.execute("SELECT 1 FROM kakao_intake_events WHERE request_key=%s", (request_key,)).fetchone():
            return "duplicate", None
        if session["pending_event"]:
            return "busy", None
        event_id = uuid4().hex
        conn.execute("""INSERT INTO kakao_intake_events
            (event_id,request_key,bot_id,user_id,message,extra,callback_url) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (event_id, request_key, bot_id, user_id, message, Jsonb(extra), callback_url))
        conn.execute("UPDATE kakao_intake_sessions SET pending_event=%s WHERE bot_id=%s AND user_id=%s",
                     (event_id, bot_id, user_id))
        return "accepted", event_id


def begin(event_id):
    with connect() as conn:
        event = conn.execute("UPDATE kakao_intake_events SET status='running' WHERE event_id=%s RETURNING *",
                             (event_id,)).fetchone()
        state = conn.execute("SELECT state FROM kakao_intake_sessions WHERE bot_id=%s AND user_id=%s",
                             (event["bot_id"], event["user_id"])).fetchone()["state"]
        return event, state


def complete(event, turn):
    """신고 저장과 대화 상태 변경을 함께 커밋해 중복 접수를 막는다."""
    state, answer = turn.state, turn.answer
    with connect() as conn:
        if turn.submit:
            fields = LostReport.model_validate(state["fields"]).model_dump(exclude_none=True)
            conn.execute("""INSERT INTO lost_reports (draft_id,bot_id,user_id,fields) VALUES (%s,%s,%s,%s)
                ON CONFLICT (draft_id) DO NOTHING""",
                (state["draft_id"], event["bot_id"], event["user_id"], Jsonb(fields)))
            number = conn.execute("SELECT id FROM lost_reports WHERE draft_id=%s", (state["draft_id"],)).fetchone()["id"]
            state["report_id"] = f"L-{number:06d}"
            answer["text"] = answer["text"].replace("{report_id}", state["report_id"])
        conn.execute("""UPDATE kakao_intake_sessions SET state=%s, last_reply=%s, pending_event=NULL
            WHERE bot_id=%s AND user_id=%s AND pending_event=%s""",
            (Jsonb(state), Jsonb(answer), event["bot_id"], event["user_id"], event["event_id"]))
        conn.execute("UPDATE kakao_intake_events SET status='done', reply=%s WHERE event_id=%s",
                     (Jsonb(answer), event["event_id"]))
    return answer


def fail(event_id, answer):
    # AI 오류 때는 사용자가 이전에 입력한 대화 상태를 유지한다.
    with connect() as conn:
        conn.execute("UPDATE kakao_intake_events SET status='failed', reply=%s WHERE event_id=%s",
                     (Jsonb(answer), event_id))
        conn.execute("UPDATE kakao_intake_sessions SET pending_event=NULL, last_reply=%s WHERE pending_event=%s",
                     (Jsonb(answer), event_id))


def latest_reply(bot_id, user_id):
    with connect() as conn:
        return conn.execute("SELECT pending_event,last_reply FROM kakao_intake_sessions WHERE bot_id=%s AND user_id=%s",
                            (bot_id, user_id)).fetchone()


def record_callback(event_id, status):
    with connect() as conn:
        conn.execute("UPDATE kakao_intake_events SET callback_status=%s WHERE event_id=%s", (status, event_id))
