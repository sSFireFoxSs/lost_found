"""습득물 공고 MCP 서버. 공고 읽기는 Resource, 공고 게시는 Tool.

게시된 공고는 data/notices.json에 저장해 서버를 다시 켜도 남는다.
"""
import json
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError

mcp = MCPServer("lost_found")

DATA = Path(__file__).resolve().parent.parent / "data" / "notices.json"   # 어느 폴더에서 켜도 같은 파일
saved = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
NOTICES = saved.get("notices", {})                # 게시된 공고
VERIFY = saved.get("verify", {})                  # 본인확인용 특징. 공고(Resource)로는 공개하지 않음


def save():
    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text(json.dumps({"notices": NOTICES, "verify": VERIFY}, ensure_ascii=False, indent=1), encoding="utf-8")


@mcp.resource("lost://notices/{notice_id}", mime_type="application/json")
def notice_info(notice_id: str) -> dict:
    """게시된 습득물 공고 한 건: 분류, 물품명, 색상, 습득일, 습득장소, 접수기관, 공개 특징, 상세내용."""
    if notice_id not in NOTICES:
        raise ResourceError(f"{notice_id} 공고를 찾을 수 없습니다.")
    return NOTICES[notice_id]


@mcp.tool()
def publish_notice(notice: dict, verify_features: dict) -> dict:
    """습득물 공고를 게시한다. 데이터가 바뀌므로 담당자 승인을 받은 뒤에만 호출한다."""
    notice_id = f"F-{len(NOTICES) + 1:04d}"
    NOTICES[notice_id] = {"notice_id": notice_id, **notice}
    VERIFY[notice_id] = verify_features
    save()
    return {"notice_id": notice_id, "count": len(NOTICES)}


if __name__ == "__main__":
    mcp.run("streamable-http", port=8801)        # http://127.0.0.1:8801/mcp
