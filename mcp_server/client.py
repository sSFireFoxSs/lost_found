"""MCP 클라이언트. 에이전트는 서버를 직접 부르지 않고 이 함수들을 거친다 (수업 mcp_client.py와 같은 구조)."""
import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor

from mcp import Client, MCPError

MCP_URL = "http://127.0.0.1:8801/mcp"


def request(job):
    """서버에 연결해 job(client)을 실행한다. 이벤트 루프가 이미 돌 수 있어 별도 스레드에서 실행."""
    async def connect_and_run():
        async with Client(MCP_URL) as client:
            return await job(client)

    with ThreadPoolExecutor(max_workers=1) as pool: # FastAPI 혹은 LangGraph에서 비동기가 돌 수 있을수 있는데 또 부르면 에러 날 수 있음. 그래서 새 일꾼 부름
        return pool.submit(asyncio.run, connect_and_run()).result()


# ---- 가리기: 공고에 섞여 들어갈 수 있는 식별 정보 ----
PATTERNS = [
    (re.compile(r"(01\d)-?\d{3,4}-?(\d{4})"), r"\1-****-\2"),                  # 휴대폰 번호
    (re.compile(r"\b(\d{4})[- ]?\d{4}[- ]?\d{4}[- ]?(\d{4})\b"), r"\1-****-****-\2"),  # 카드 번호
    (re.compile(r"(\d{6})-?[1-4]\d{6}"), r"\1-*******"),                       # 주민등록번호
]


def mask(text: str) -> str:
    for pattern, repl in PATTERNS:
        text = pattern.sub(repl, text)
    return text


# ---- 호출 허용 목록: 공고 게시는 승인 노드만 ----
ALLOWED = {
    "registration_agent": set(),
    "verification_agent": set(),
    "privacy_agent": set(),
    "approval": {"publish_notice"},
}


def is_allowed(caller: str, tool: str) -> bool: # 담당자가 승인해야만 올라가는 부분을 코드로 막음
    return tool in ALLOWED.get(caller, set()) # is_allowed("approval", "publish_notice") 만 허용


def read_resource(uri: str) -> dict:
    """예) read_resource("lost://notices/F-0001")"""
    async def read(client):
        try:
            result = await client.read_resource(uri)
        except MCPError as error:
            return {"ok": False, "text": str(error), "data": None}
        text = result.contents[0].text
        return {"ok": True, "text": mask(text), "data": json.loads(text)}
    return request(read)


def call_tool(caller: str, tool: str, args: dict) -> dict:
    """허용 목록 확인 → Tool 호출 → 출력 가리기."""
    if not is_allowed(caller, tool):
        return {"ok": False, "text": f"차단: {caller}은(는) {tool}을(를) 호출할 수 없습니다.", "data": None}
    result = request(lambda client: client.call_tool(tool, args))
    text = result.content[0].text
    if result.is_error:
        return {"ok": False, "text": text, "data": None}
    return {"ok": True, "text": mask(text), "data": json.loads(text)}
