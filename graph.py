"""습득물 공고 표준화 멀티에이전트 (LangGraph).

registration → verification ─(불일치, 재시도 남음)→ registration
                            └→ privacy → approval(interrupt, 담당자 승인) → END
"""
import operator
import os
from typing import Annotated, Literal, TypedDict

from dotenv import find_dotenv, load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from mcp_client import call_tool, mask

load_dotenv(find_dotenv())
MODEL = os.getenv("MCP_LAB_MODEL", "gpt-6-luna")
REASONING = {"reasoning_effort": "none"} if MODEL.startswith("gpt-6") else {}
llm = ChatOpenAI(model=MODEL, temperature=0, timeout=60, **REASONING)

MAX_RETRY = 2                                     # 검증 실패 시 재추출 상한 (노션: 초안 값)
UNKNOWN = "확인 불가"

# ---- 물품 유형별 템플릿: 어느 기관이 등록해도 같은 항목을 채운다 ----
CATEGORIES = ["휴대폰", "카드", "지갑", "가방", "우산", "의류", "전자기기", "귀금속", "증명서", "기타"]
TEMPLATE = {
    "휴대폰": ["기종", "색상", "케이스", "액정 상태", "잠금화면 배경", "특이사항"],
    "카드": ["카드사", "카드 종류", "색상", "카드 번호", "명의자"],
    "지갑": ["색상", "브랜드", "재질", "내용물", "특이사항"],
    "증명서": ["종류", "발급기관", "명의자", "번호"],
}
DEFAULT_TEMPLATE = ["색상", "브랜드", "재질/형태", "크기", "특이사항"]
ALWAYS_PRIVATE = {"카드 번호", "명의자", "번호", "이름", "전화번호", "주민등록번호"}   # 모델 판단과 상관없이 코드로 비공개


def template_for(category: str) -> list[str]:
    return TEMPLATE.get(category, DEFAULT_TEMPLATE)


# ---- State ----
class NoticeState(TypedDict, total=False):
    report: dict                                  # 신고서: date, place, finder_name, memo
    photo_path: str                               # 업로드한 사진 경로 (VLM 판독 전)
    item: dict                                    # {"category", "item_name", "features": {항목: 값}}
    problems: list[str]                           # 검증에서 나온 불일치
    tries: int                                    # 재추출 횟수
    draft: dict                                   # 공고 초안: public / verify / private
    approve: str                                  # 담당자 결정: "승인" / "거절"
    notice_id: str
    status: str                                   # published / rejected
    handoffs: Annotated[list[dict], operator.add]


def handoff(from_agent, to_agent, task):
    return {"from_agent": from_agent, "to_agent": to_agent, "task": task}


# ---- VLM 자리 (비워 둠) ----
def describe_photo(photo_path: str | None) -> str:
    """사진에서 보이는 특징을 글로 바꾼다.
    shortcut: VLM 미구현이라 빈 문자열. 다음 단계에서 이미지 입력 모델로 교체."""
    return ""


def source_text(state) -> str:
    r = state["report"]
    return (f"습득일: {r['date']}\n습득장소: {r['place']}\n담당자 메모: {r.get('memo', '')}\n"
            f"사진 판독: {describe_photo(state.get('photo_path')) or '(없음)'}")


# ---- ① Registration Agent: 정보 최대 확보 ----
class Feature(BaseModel):
    name: str = Field(description="템플릿 항목 이름")
    value: str = Field(description=f"근거가 있는 값. 근거가 없으면 '{UNKNOWN}'")


class Extract(BaseModel):
    category: Literal[tuple(CATEGORIES)] = Field(description="물품 분류")
    item_name: str = Field(description="짧은 물품명 (예: 검정 장우산)")
    features: list[Feature]


REG_PROMPT = ("습득물 등록 담당입니다. 아래 자료에 적힌 내용만 근거로 물품 분류와 특징을 뽑으세요. "
              f"분류별 템플릿 항목을 모두 채우고, 근거가 없는 항목은 '{UNKNOWN}'로 쓰세요. 지어내지 마세요.\n"
              f"템플릿: { {c: template_for(c) for c in CATEGORIES} }")
reg_llm = llm.with_structured_output(Extract, method="function_calling")


def registration(state):
    prompt = REG_PROMPT
    if state.get("problems"):                     # 검증에서 돌아온 경우 지적 사항을 함께 준다
        prompt += "\n이전 추출의 문제: " + "; ".join(state["problems"])
    r = reg_llm.invoke([SystemMessage(prompt), HumanMessage(source_text(state))])
    got = {f.name: f.value for f in r.features}
    features = {k: got.get(k) or UNKNOWN for k in template_for(r.category)}   # 템플릿 항목은 코드로 보장
    item = {"category": r.category, "item_name": r.item_name, "features": features}
    return {"item": item, "handoffs": [handoff("registration_agent", "verification_agent", "추출 결과 검증")]}


# ---- ② Verification Agent: 정확성 ----
class Check(BaseModel):
    ok: bool = Field(description="모든 값이 자료로 뒷받침되면 true")
    problems: list[str] = Field(description="자료에 없는데 적힌 값, 분류 오류 등. 없으면 빈 목록")


VER_PROMPT = (f"습득물 검증 담당입니다. 추출 결과의 각 값이 자료에 근거가 있는지 확인하세요. "
              f"'{UNKNOWN}'은 문제가 아닙니다. 자료에 없는 값을 지어냈거나 분류가 틀렸으면 problems에 적으세요.")
ver_llm = llm.with_structured_output(Check, method="function_calling")


def verification(state):
    c = ver_llm.invoke([SystemMessage(VER_PROMPT),
                        HumanMessage(f"[자료]\n{source_text(state)}\n\n[추출 결과]\n{state['item']}")])
    if c.ok:
        return {"problems": [], "handoffs": [handoff("verification_agent", "privacy_agent", "공개 범위 판단")]}
    return {"problems": c.problems, "tries": state.get("tries", 0) + 1}


def after_verification(state):
    if not state["problems"]:
        return "privacy"
    return "registration" if state["tries"] <= MAX_RETRY else "privacy"   # 상한 넘으면 문제를 달고 담당자에게


# ---- ③ Privacy Agent: 노출 최소화 ----
class Level(BaseModel):
    name: str = Field(description="항목 이름만, 값은 빼고 (예: 색상)")
    level: Literal["공개", "본인확인용", "비공개"]


class Disclosure(BaseModel):
    levels: list[Level]


PRIV_PROMPT = ("습득물 공고의 공개 범위 담당입니다. 각 특징을 분류하세요.\n"
               "- 공개: 분실자가 검색·매칭에 쓰는 일반 특징 (색상, 브랜드, 형태, 기종)\n"
               "- 본인확인용: 주인만 알 만한 세부 (내용물, 잠금화면, 스티커 문구, 흠집 위치) — 공고에 올리지 않고 수령 때 질문으로 씀\n"
               "- 비공개: 사람을 식별하는 정보 (이름, 카드·전화·증명서 번호)")
priv_llm = llm.with_structured_output(Disclosure, method="function_calling")


def privacy(state):
    features = {k: v for k, v in state["item"]["features"].items() if v != UNKNOWN}
    d = priv_llm.invoke([SystemMessage(PRIV_PROMPT), HumanMessage(str(features))])
    level = {x.name: x.level for x in d.levels}
    return {"draft": build_draft(state["report"], state["item"], features, level)}


def build_draft(report, item, features, level):
    """모델 판단 위에 코드 규칙을 덮어쓴다: 식별 항목은 무조건 비공개, 공개 값은 가리기, 습득자명은 넣지 않음."""
    draft = {"public": {}, "verify": {}, "private": []}
    for name, value in features.items():
        lv = "비공개" if name in ALWAYS_PRIVATE else level.get(name, "본인확인용")   # 판단 누락은 안전한 쪽으로
        if lv == "공개":
            draft["public"][name] = mask(value)
        elif lv == "본인확인용":
            draft["verify"][name] = mask(value)  # 자유 서술 안에 섞인 번호도 저장 전에 가림
        else:
            draft["private"].append(name)
    draft["notice"] = {"category": item["category"], "item_name": mask(item["item_name"]),
                       "date": report["date"], "place": report["place"], "features": draft["public"]}
    return draft


# ---- ④ 담당자 승인 → MCP Tool로 게시 ----
def approval(state):
    decision = interrupt({"draft": state["draft"], "problems": state.get("problems", [])})
    if decision != "승인":
        return {"approve": decision, "status": "rejected"}
    result = call_tool("approval", "publish_notice",
                       {"notice": state["draft"]["notice"], "verify_features": state["draft"]["verify"]})
    return {"approve": decision, "status": "published", "notice_id": result["data"]["notice_id"]}


def assemble(checkpointer=None):
    builder = StateGraph(NoticeState)
    for name, fn in [("registration", registration), ("verification", verification),
                     ("privacy", privacy), ("approval", approval)]:
        builder.add_node(name, fn)
    builder.add_edge(START, "registration")
    builder.add_edge("registration", "verification")
    builder.add_conditional_edges("verification", after_verification,
                                  {"registration": "registration", "privacy": "privacy"})
    builder.add_edge("privacy", "approval")
    builder.add_edge("approval", END)
    return builder.compile(checkpointer=checkpointer)


graph = assemble(InMemorySaver())
