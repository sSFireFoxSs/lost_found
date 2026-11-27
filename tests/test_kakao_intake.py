"""AI 호출 없는 대화 테스트 + 임시 PostgreSQL 스키마에서 실행하는 통합 테스트."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pydantic import ValidationError

from agents.lost_intake import Turn, build_graph, handle_message, new_state
from agents.lost_schema import ExtractedFields, LostReport
from app import intake_store as store
from app.kakao import app

FIELDS = {"item_name": "지갑", "category": "지갑", "color": "검정", "brand": "확인 불가",
          "lost_at": "2026-11-26 저녁 7시쯤", "lost_place": "강남역 2번 출구",
          "public_features": "가죽 반지갑", "private_features": "안쪽에 별 각인"}


def fake_extract(message, state):
    if message == "남색으로 바꿔주세요":
        return {"color": "남색"}
    if message == "전체 정보":
        return FIELDS
    if message == "지갑 잃어버렸어요":
        return {"item_name": "지갑", "category": "지갑"}
    return {}


def fake_handle(state, message, extra=None):
    return handle_message(state, message, extra, extract=fake_extract)


class ConversationTests(unittest.TestCase):
    def test_missing_information_and_unknown(self):
        turn = fake_handle({}, "지갑 잃어버렸어요")
        self.assertEqual(turn.state["asked_field"], "lost_at")
        turn = fake_handle(turn.state, "모름")
        self.assertEqual(turn.state["fields"]["lost_at"], "확인 불가")
        self.assertEqual(turn.state["asked_field"], "lost_place")
        self.assertFalse(turn.submit)

    def test_summary_hides_private_features_and_requires_button(self):
        turn = fake_handle({}, "전체 정보")
        self.assertEqual(turn.state["stage"], "confirming")
        self.assertNotIn("별 각인", turn.answer["text"])
        no_token = fake_handle(turn.state, "접수하기")
        self.assertFalse(no_token.submit)
        confirmed = fake_handle(no_token.state, "접수하기", {"confirm_token": no_token.state["confirm_token"]})
        self.assertTrue(confirmed.submit)

    def test_edit_invalidates_old_confirmation(self):
        original = fake_handle({}, "전체 정보")
        old_token = original.state["confirm_token"]
        editing = fake_handle(original.state, "수정하기")
        unchanged = fake_handle(editing.state, "도움말")
        self.assertEqual(unchanged.state["stage"], "editing")
        changed = fake_handle(editing.state, "남색으로 바꿔주세요")
        self.assertEqual(changed.state["fields"]["color"], "남색")
        stale = fake_handle(changed.state, "접수하기", {"confirm_token": old_token})
        self.assertFalse(stale.submit)
        self.assertEqual(stale.state["fields"]["color"], "남색")

    def test_cancel_and_restart(self):
        turn = fake_handle({}, "전체 정보")
        cancelled = fake_handle(turn.state, "접수 취소")
        self.assertEqual(cancelled.state["fields"], {})
        restarted = fake_handle(cancelled.state, "새 접수")
        self.assertNotEqual(restarted.state["draft_id"], turn.state["draft_id"])
        self.assertEqual(restarted.state["asked_field"], "item_name")

    def test_natural_input_then_short_answers_preserve_known_fields(self):
        extracts = {
            "어제 강남역에서 검은 지갑 잃어버렸어요": {
                "item_name": "지갑", "lost_at": "2026-11-26", "lost_place": "강남역", "color": "검정"},
            "가죽 반지갑이요": {"public_features": "가죽 반지갑"},
            "안쪽에 별 각인이 있어요": {"private_features": "별 각인"},
        }
        turn = None
        asked = []
        for message in extracts:
            turn = handle_message(turn.state if turn else {}, message, extract=lambda msg, _: extracts[msg])
            asked.append(turn.state["asked_field"])
        self.assertEqual(asked, ["public_features", "private_features", None])
        self.assertEqual(turn.state["fields"]["lost_place"], "강남역")
        self.assertEqual(turn.state["stage"], "confirming")
        # 선택 항목은 생략해도 확정 가능하다. 추출 모델도 같은 필드 정의를 사용한다.
        self.assertNotIn("brand", turn.state["fields"])
        self.assertEqual(set(ExtractedFields.model_fields), set(LostReport.model_fields))
        self.assertEqual(turn.state["missing_fields"], [])

    def test_invalid_values_are_reasked_and_cannot_be_confirmed(self):
        for field, bad_value in [("item_name", "모름"), ("lost_place", " "),
                                 ("color", "x" * 101), ("category", "invalid-category")]:
            with self.subTest(field=field):
                state = {**new_state(), "stage": "confirming", "confirm_token": "old-token",
                         "fields": {**FIELDS, field: bad_value}}
                turn = fake_handle(state, "접수하기", {"confirm_token": "old-token"})
                self.assertEqual(turn.state["asked_field"], field)
                self.assertFalse(turn.submit)
                self.assertIsNone(turn.state["confirm_token"])
        state = {**new_state(), "stage": "confirming", "confirm_token": "old-token", "fields": {"item_name": "지갑"}}
        turn = fake_handle(state, "접수하기", {"confirm_token": "old-token"})
        self.assertEqual(turn.state["asked_field"], "lost_at")
        self.assertFalse(turn.submit)

    def test_unknown_item_still_requires_item_type(self):
        turn = fake_handle({}, "시작")
        turn = fake_handle(turn.state, "모름")
        self.assertEqual(turn.state["asked_field"], "item_name")
        self.assertEqual(turn.state["invalid_fields"], ["item_name"])

    def test_langgraph_routes_on_validation_result(self):
        graph = build_graph(fake_extract)
        for message, terminal in [("지갑 잃어버렸어요", "ask_missing"), ("전체 정보", "summarize")]:
            updates = list(graph.stream({**new_state(), "message": message, "extra": {},
                "changed": False, "confirm_requested": False, "submit": False}, stream_mode="updates"))
            self.assertEqual([next(iter(update)) for update in updates],
                             ["route_message", "extract_information", "validate_schema", terminal])


TEST_URL = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "TEST_DATABASE_URL 설정 시 PostgreSQL 통합 테스트 실행")
class PostgresKakaoTests(unittest.TestCase):
    def setUp(self):
        self.schema = "test_kakao_" + uuid4().hex
        with psycopg.connect(TEST_URL, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        url = make_conninfo(TEST_URL, options=f"-c search_path={self.schema}")
        env = patch.dict(os.environ, {"DATABASE_URL": url, "KAKAO_SKILL_SECRET": "test-secret", "KAKAO_BOT_ID": "test-bot"})
        env.start()
        self.addCleanup(env.stop)
        handle = patch("app.kakao.handle_message", side_effect=fake_handle)
        handle.start()
        self.addCleanup(handle.stop)
        self.headers = {"X-Skill-Key": "test-secret"}

    def drop_schema(self):
        with psycopg.connect(TEST_URL, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def payload(self, message, user="alice", extra=None, callback=None):
        return {"bot": {"id": "test-bot"}, "userRequest": {"user": {"id": user},
                "utterance": message, "callbackUrl": callback}, "action": {"clientExtra": extra}}

    def send(self, client, message, **kwargs):
        return client.post("/kakao/skill", json=self.payload(message, **kwargs), headers=self.headers)

    def last(self, client, user="alice"):
        return self.send(client, "결과 확인", user=user).json()["template"]

    def test_complete_flow_duplicate_click_and_restart(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").status_code, 200)
            initial = self.send(client, "전체 정보")
            self.assertEqual(initial.status_code, 200)
            summary = self.last(client)
            token = summary["quickReplies"][0]["extra"]
            self.assertIn("접수할까요", summary["outputs"][0]["simpleText"]["text"])
            self.send(client, "접수하기", extra=token)
            result = self.last(client)
            self.assertIn("L-000001", str(result))
            self.send(client, "접수하기", extra=token)
            with store.connect() as conn:
                self.assertEqual(conn.execute("SELECT count(*) AS n FROM lost_reports").fetchone()["n"], 1)
                fields = conn.execute("SELECT fields FROM lost_reports").fetchone()["fields"]
                self.assertEqual(fields["private_features"], "안쪽에 별 각인")
        with TestClient(app) as client:
            self.assertIn("L-000001", str(self.last(client)))
            self.assertNotIn("L-000001", str(self.last(client, user="bob")))

    def test_api_authentication_and_validation(self):
        with TestClient(app) as client:
            self.assertEqual(client.post("/kakao/skill", json=self.payload("시작")).status_code, 401)
            wrong_bot = self.payload("시작")
            wrong_bot["bot"]["id"] = "another-bot"
            self.assertEqual(client.post("/kakao/skill", json=wrong_bot, headers=self.headers).status_code, 403)
            for url in ["http://127.0.0.1/admin", "https://kakao.com.evil.test/callback", "https://bot-api.kakao.com:8443/x"]:
                self.assertEqual(self.send(client, "시작", callback=url).status_code, 422)
            self.assertEqual(self.send(client, " " * 3).status_code, 422)
            self.assertEqual(self.send(client, "x" * 2001).status_code, 422)

    def test_callback_response_delivery_and_deduplication(self):
        with TestClient(app) as client, patch("app.kakao.httpx.Client") as http:
            remote = http.return_value.__enter__.return_value
            remote.post.return_value.json.return_value = {"status": "SUCCESS"}
            callback = "https://bot-api.kakao.com/callback/test-only"
            result = self.send(client, "전체 정보", callback=callback)
            self.assertEqual(result.json(), {"version": "2.0", "useCallback": True})
            self.assertEqual(remote.post.call_count, 1)
            sent = remote.post.call_args.kwargs["json"]
            self.assertEqual(sent["version"], "2.0")
            self.assertIn("지갑", str(sent))
            self.send(client, "전체 정보", callback=callback)
            self.assertEqual(remote.post.call_count, 1)
            with store.connect() as conn:
                self.assertEqual(conn.execute("SELECT callback_status FROM kakao_intake_events").fetchone()["callback_status"], "sent")

    def test_failed_callback_keeps_pollable_answer(self):
        with TestClient(app) as client, patch("app.kakao.httpx.Client") as http:
            http.return_value.__enter__.return_value.post.return_value.json.return_value = {"status": "FAIL"}
            self.send(client, "전체 정보", callback="https://bot-api.kakao.com/callback/failure")
            self.assertIn("지갑", str(self.last(client)))
            with store.connect() as conn:
                self.assertEqual(conn.execute("SELECT callback_status FROM kakao_intake_events").fetchone()["callback_status"], "failed")

    def test_model_failure_preserves_previous_fields(self):
        with TestClient(app) as client:
            self.send(client, "지갑 잃어버렸어요")
            with patch("app.kakao.handle_message", side_effect=RuntimeError("synthetic failure")):
                self.send(client, "어제요")
            self.assertIn("문제가 생겼", str(self.last(client)))
            with store.connect() as conn:
                state = conn.execute("SELECT state FROM kakao_intake_sessions").fetchone()["state"]
                self.assertEqual(state["fields"]["item_name"], "지갑")

    def test_only_one_pending_message_per_user(self):
        store.initialize()
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _: store.enqueue("test-bot", "alice", "시작", {}, None)[0], range(12)))
        self.assertEqual(outcomes.count("accepted"), 1)
        self.assertEqual(outcomes.count("busy"), 11)
        store.recover()
        self.assertIsNone(store.latest_reply("test-bot", "alice")["pending_event"])
        self.assertEqual(store.enqueue("test-bot", "alice", "시작", {}, None)[0], "accepted")

    def test_unfinished_conversation_continues_after_restart(self):
        with TestClient(app) as client:
            self.send(client, "지갑 잃어버렸어요")
            self.assertIn("언제", str(self.last(client)))
        with TestClient(app) as client:
            self.send(client, "모름")
            self.assertIn("어디", str(self.last(client)))
            with store.connect() as conn:
                state = conn.execute("SELECT state FROM kakao_intake_sessions").fetchone()["state"]
                self.assertEqual(state["fields"], {"item_name": "지갑", "category": "지갑", "lost_at": "확인 불가"})

    def test_storage_rejects_invalid_confirmed_report(self):
        store.initialize()
        _, event_id = store.enqueue("test-bot", "alice", "접수하기", {}, None)
        event, _ = store.begin(event_id)
        invalid = Turn({**new_state(), "fields": {"item_name": "지갑"}}, {"text": "완료"}, submit=True)
        with self.assertRaises(ValidationError):
            store.complete(event, invalid)
        with store.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM lost_reports").fetchone()["n"], 0)
            self.assertEqual(conn.execute("SELECT status FROM kakao_intake_events").fetchone()["status"], "running")


if __name__ == "__main__":
    unittest.main()
