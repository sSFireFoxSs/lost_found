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
print("규칙 테스트 통과")
