"""습득물 공고 MCP 서버. 공고 읽기는 Resource, 공고 게시는 Tool (수업 library_mcp.py와 같은 구조)."""
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError

mcp = MCPServer("lost_found")

NOTICES = {}                                      # 게시된 공고. 서버 메모리에 있어서 다시 켜면 비워짐
VERIFY = {}                                       # 본인확인용 특징. 공고(Resource)로는 공개하지 않음


@mcp.resource("lost://notices/{notice_id}", mime_type="application/json")
def notice_info(notice_id: str) -> dict:
    """게시된 습득물 공고 한 건: 분류, 물품명, 습득일, 습득장소, 공개 특징."""
    if notice_id not in NOTICES:
        raise ResourceError(f"{notice_id} 공고를 찾을 수 없습니다.")
    return NOTICES[notice_id]


@mcp.tool()
def publish_notice(notice: dict, verify_features: dict) -> dict:
    """습득물 공고를 게시한다. 데이터가 바뀌므로 담당자 승인을 받은 뒤에만 호출한다."""
    notice_id = f"F-{len(NOTICES) + 1:04d}"
    NOTICES[notice_id] = {"notice_id": notice_id, **notice}
    VERIFY[notice_id] = verify_features
    return {"notice_id": notice_id, "count": len(NOTICES)}


if __name__ == "__main__":
    mcp.run("streamable-http", port=8801)        # http://127.0.0.1:8801/mcp
