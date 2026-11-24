"""모델 없이 확인하는 코드 규칙 (가리기, 허용 목록, 공개 범위 덮어쓰기). 실행: python test_rules.py"""
from graph import build_draft
from mcp_client import is_allowed, mask

assert mask("연락처 010-2345-6789") == "연락처 010-****-6789"
assert mask("1234-5678-9012-3456") == "1234-****-****-3456"
assert not is_allowed("registration_agent", "publish_notice")
assert not is_allowed("privacy_agent", "publish_notice")
assert is_allowed("approval", "publish_notice")

report = {"date": "2026-11-24", "place": "2호선 강남역", "finder_name": "홍길동"}
item = {"category": "카드", "item_name": "신한 체크카드", "features": {}}
features = {"카드사": "신한", "카드 번호": "1234-5678-9012-3456", "명의자": "김분실", "색상": "파랑"}
level = {"카드사": "공개", "카드 번호": "공개", "명의자": "공개"}          # 모델이 잘못 판단해도
d = build_draft(report, item, features, level)
assert d["private"] == ["카드 번호", "명의자"]                          # 코드가 비공개로 막음
assert d["verify"] == {"색상": "파랑"}                                  # 판단 누락 → 본인확인용
assert "홍길동" not in str(d["notice"])                                 # 습득자명은 공고에 없음
print("규칙 테스트 통과")
