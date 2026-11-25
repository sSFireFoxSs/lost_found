"""게시된 공고 조회 API (MCP Resource 읽기)."""
from fastapi import APIRouter

from mcp_server.client import read_resource

router = APIRouter(prefix="/api")


@router.get("/notices/{notice_id}")
def notice(notice_id: str):
    return read_resource(f"lost://notices/{notice_id}")
