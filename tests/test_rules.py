"""모델 없이 확인하는 코드 규칙. 실행(프로젝트 루트에서): .venv/bin/python -m tests.test_rules""" #test
from agents.graph import after_approval, after_verification
from agents.nodes import build_draft
from mcp_server.client import is_allowed, mask

# 가리기 · 허용 목록
assert mask("연락처 010-2345-6789") == "연락처 010-****-6789"
assert mask("1234-5678-9012-3456") == "1234-****-****-3456"
assert not is_allowed("registration_agent", "publish_notice")
assert not is_allowed("privacy_agent", "publish_notice")
assert is_allowed("approval", "publish_notice")

# 공개 범위: 모델·담당자가 잘못 판단해도 코드가 막음
report = {"date": "2026-11-24", "place": "2호선 강남역", "finder_name": "홍길동"}
item = {"category": "카드", "item_name": "신한 체크카드", "features": {}}
features = {"카드사": "신한", "카드 번호": "1234-5678-9012-3456", "명의자": "김분실", "색상": "파랑"}
level = {"카드사": "공개", "카드 번호": "공개", "명의자": "공개"}
d = build_draft(report, item, features, level)
assert d["private"] == ["카드 번호", "명의자"]
assert d["verify"] == {"색상": "파랑"}                                  # 판단 누락 → 본인확인용
assert "홍길동" not in str(d["notice"])                                 # 습득자명은 공고에 없음

# 분기: 검증 재시도 상한, 담당자 결정
assert after_verification({"problems": [], "tries": 0}) == "privacy"
assert after_verification({"problems": ["x"], "tries": 1}) == "registration"
assert after_verification({"problems": ["x"], "tries": 3}) == "privacy"
assert after_approval({"status": "revising"}) == "registration"
assert after_approval({"status": "revise_limit"}) == "approval"
assert after_approval({"status": "published"}) == "end"
assert after_approval({"status": "discarded"}) == "end"
# 상세내용 문구: 경찰민원24 표준 문장 틀 그대로, 특이사항에는 공개 특징만
from agents.categories import notice_text
t = notice_text("강남역(2호선)", "2026-11-25", "컨버스 장우산", "블루(파랑)색", {"재질/형태": "나무 손잡이"})
assert t.startswith("강남역(2호선)에서는 [2026.11.25] [컨버스 장우산(블루(파랑)색)]을 습득/보관 하였습니다.")
assert t.endswith("특이사항 : 재질/형태 나무 손잡이")
assert notice_text("A", "2026-11-25", "지갑", "블랙(검정)색", {}).endswith("특이사항 : 없음")
assert "김분실" not in d["notice"]["content"] and "5678" not in d["notice"]["content"]   # 비공개 항목은 문장에도 없음
# 물품명에 이미 든 값·제조사·모델명은 특이사항에 되풀이하지 않음
t = notice_text("A", "2026-11-26", "애플 에어팟 맥스 헤드폰", "블루(파랑)색",
                {"제조사": "Apple", "모델명": "Apple AirPods Max", "형태": "헤드폰"})
assert t.endswith("특이사항 : 없음"), t

# 주인만 알 세부(특이사항·내용물 등)는 모델이 '공개'라 해도 본인확인용 + 분류별 확인 방법
item = {"category": "전자기기", "item_name": "애플 에어팟 맥스 헤드폰", "color": "블루(파랑)색", "features": {}}
d = build_draft(report, item, {"모델명": "Apple AirPods Max", "특이사항": "메시형 헤드밴드 패드"},
                {"모델명": "공개", "특이사항": "공개"})
assert d["public"] == {"모델명": "Apple AirPods Max"} and d["verify"] == {"특이사항": "메시형 헤드밴드 패드"}
assert "메시형" not in d["notice"]["content"]
assert any("블루투스" in m for m in d["handover"]["methods"])
print("규칙 테스트 통과")
