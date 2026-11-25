"""에이전트 노드. 노드는 State를 받아 바꿀 값만 돌려준다."""
import base64
import mimetypes
import os
from pathlib import Path
from typing import Literal

from dotenv import find_dotenv, load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from agents.categories import ALWAYS_PRIVATE, CATEGORIES, template_for
from agents.state import handoff
from mcp_server.client import call_tool, mask

load_dotenv(find_dotenv())
MODEL = os.getenv("MCP_LAB_MODEL", "gpt-6-luna")
REASONING = {"reasoning_effort": "none"} if MODEL.startswith("gpt-6") else {}
llm = ChatOpenAI(model=MODEL, temperature=0, timeout=60, **REASONING)

UNKNOWN = "확인 불가"


# ---- 사진 판독 (VLM): 같은 모델·같은 API 키에 이미지를 함께 보낸다 ----
VLM_PROMPT = ("습득물 사진 판독 담당입니다. 사진에 실제로 보이는 것만 짧은 문장으로 적으세요: "
              "물품 종류, 색상, 브랜드·로고, 재질·형태, 크기 짐작, 흠집·스티커·글자 같은 특이점. "
              "보이지 않는 것은 짐작하지 마세요. 사람 이름·카드번호 같은 글자가 보이면 '식별 정보 있음'이라고만 쓰세요.")


def describe_photo(photo_path: str | None) -> str:
    """사진에서 보이는 특징을 글로 바꾼다. 실패해도 메모로 계속 진행하도록 사유를 글로 돌려준다."""
    if not photo_path:
        return ""
    mime = mimetypes.guess_type(photo_path)[0] or "image/jpeg"
    data = base64.b64encode(Path(photo_path).read_bytes()).decode()
    try:
        r = llm.invoke([SystemMessage(VLM_PROMPT), HumanMessage(content=[
            {"type": "text", "text": "이 습득물 사진을 판독하세요."},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}},
        ])])
        return r.text
    except Exception as e:                        # 판독 실패로 등록 전체가 실패하지 않게
        return f"(사진 판독 실패: {type(e).__name__})"


def source_text(state) -> str:
    """에이전트에게 주는 근거 자료: 신고서 + 담당자 메모·체크 사항 + 사진 판독."""
    r = state["report"]
    chosen = " > ".join(x for x in [r.get("category_main"), r.get("category_sub")] if x) or "(선택 안 함)"
    return (f"습득일: {r['date']}\n습득장소: {r['place']}\n담당자가 고른 분류: {chosen}\n"
            f"담당자 특징 메모: {r.get('memo') or '(없음)'}\n"
            f"담당자 체크 사항: {', '.join(r.get('checks', [])) or '(없음)'}\n"
            f"사진 판독: {state.get('photo_desc') or '(없음)'}\n"
            f"담당자 수정 요청(메모보다 우선): {state.get('feedback') or '(없음)'}")   # 검증도 이걸 근거로 봐야 루프가 안 돎


# ---- ① Registration Agent: 정보 최대 확보 ----
class Feature(BaseModel):
    name: str = Field(description="템플릿 항목 이름")
    value: str = Field(description=f"근거가 있는 값. 근거가 없으면 '{UNKNOWN}'")


class Extract(BaseModel):
    category: Literal[tuple(CATEGORIES)] = Field(description="대분류")
    sub_category: str = Field(description="대분류에 속한 소분류. 모르면 빈 문자열")
    item_name: str = Field(description="짧은 물품명 (예: 검정 장우산)")
    features: list[Feature]


REG_PROMPT = ("습득물 등록 담당입니다. 아래 자료에 적힌 내용만 근거로 물품 분류와 특징을 뽑으세요. "
              "담당자가 고른 분류가 있으면 그대로 쓰고, 담당자 수정 요청이 있으면 메모보다 우선하세요. "
              f"분류별 템플릿 항목을 모두 채우고, 근거가 없는 항목은 '{UNKNOWN}'로 쓰세요. 지어내지 마세요.\n"
              f"분류(대분류: 소분류): {CATEGORIES}\n"
              f"템플릿: { {c: template_for(c) for c in CATEGORIES} }")
reg_llm = llm.with_structured_output(Extract, method="function_calling")


def registration(state):
    if "photo_desc" not in state:                 # 사진은 처음 한 번만 판독 (재추출·수정 요청 때 다시 부르지 않음)
        state = {**state, "photo_desc": describe_photo(state.get("photo_path"))}
    prompt = REG_PROMPT
    if state.get("problems"):                     # 검증에서 돌아온 경우
        prompt += "\n이전 추출의 문제: " + "; ".join(state["problems"])
    r = reg_llm.invoke([SystemMessage(prompt), HumanMessage(source_text(state))])

    report = state["report"]
    category = report.get("category_main") or r.category          # 담당자 선택이 우선
    sub = report.get("category_sub") or r.sub_category
    if sub not in CATEGORIES[category]:                             # 목록에 없는 소분류는 버림
        sub = ""
    got = {f.name: f.value for f in r.features}
    features = {k: got.get(k) or UNKNOWN for k in template_for(category)}   # 템플릿 항목은 코드로 보장
    item = {"category": category, "sub_category": sub, "item_name": r.item_name, "features": features}
    return {"item": item, "photo_desc": state["photo_desc"],
            "handoffs": [handoff("registration_agent", "verification_agent", "추출 결과 검증")]}


# ---- ② Verification Agent: 정확성 + 담당자가 확인할 점 ----
class Check(BaseModel):
    ok: bool = Field(description="모든 값이 자료로 뒷받침되면 true")
    problems: list[str] = Field(description="자료에 없는데 적힌 값, 분류 오류 등. 없으면 빈 목록")
    to_check: list[str] = Field(description="담당자가 실물을 보고 확인하면 공고가 좋아질 점 (최대 3개). "
                                            "예: 메모에 브랜드가 없음, 내용물 확인 필요")


VER_PROMPT = (f"습득물 검증 담당입니다. 추출 결과의 각 값이 자료에 근거가 있는지 확인하세요. "
              f"'{UNKNOWN}'은 문제가 아닙니다. 자료에 없는 값을 지어냈거나 분류가 틀렸으면 problems에 적으세요. "
              f"'{UNKNOWN}'으로 남은 중요한 항목은 담당자가 확인할 점(to_check)으로 적으세요.")
ver_llm = llm.with_structured_output(Check, method="function_calling")


def verification(state):
    c = ver_llm.invoke([SystemMessage(VER_PROMPT),
                        HumanMessage(f"[자료]\n{source_text(state)}\n\n[추출 결과]\n{state['item']}")])
    if c.ok:
        return {"problems": [], "to_check": c.to_check,
                "handoffs": [handoff("verification_agent", "privacy_agent", "공개 범위 판단")]}
    return {"problems": c.problems, "to_check": c.to_check, "tries": state.get("tries", 0) + 1}


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
    return {"draft": build_draft(state["report"], state["item"], features, level),
            "handoffs": [handoff("privacy_agent", "approval", "담당자 검토")]}


def build_draft(report, item, features, level):
    """판단(모델 또는 담당자) 위에 코드 규칙을 덮어쓴다: 식별 항목은 무조건 비공개, 값은 가리기, 습득자명은 넣지 않음."""
    draft = {"public": {}, "verify": {}, "private": []}
    for name, value in features.items():
        lv = "비공개" if name in ALWAYS_PRIVATE else level.get(name, "본인확인용")   # 판단 누락은 안전한 쪽으로
        if lv == "공개":
            draft["public"][name] = mask(value)
        elif lv == "본인확인용":
            draft["verify"][name] = mask(value)  # 자유 서술 안에 섞인 번호도 저장 전에 가림
        else:
            draft["private"].append(name)
    draft["notice"] = {"category": item["category"], "sub_category": item.get("sub_category", ""),
                       "item_name": mask(item["item_name"]),
                       "date": report["date"], "place": report["place"], "features": draft["public"]}
    return draft


# ---- ④ 담당자 결정: 승인 / 수정후승인 / 수정요청 / 폐기 ----
MAX_REVISE = 2                                    # 담당자 수정 요청으로 다시 도는 상한 (초안 값)


def approval(state):
    d = interrupt({"draft": state["draft"], "problems": state.get("problems", []),
                   "to_check": state.get("to_check", []), "revisions": state.get("revisions", 0),
                   "photo_desc": state.get("photo_desc", "")})
    act = d["action"]

    if act == "승인":
        return publish(state["draft"])

    if act == "수정후승인":                        # 담당자가 고친 값 → 모델 없이 코드 규칙만 적용
        features = {**d.get("public", {}), **d.get("verify", {})}
        level = {k: "공개" for k in d.get("public", {})} | {k: "본인확인용" for k in d.get("verify", {})}
        item = {**state["item"], "item_name": d.get("item_name") or state["item"]["item_name"]}
        return publish(build_draft(state["report"], item, features, level))

    if act == "수정요청":                          # 담당자 코멘트를 들고 처음(추출)부터 다시
        if state.get("revisions", 0) >= MAX_REVISE:
            return {"status": "revise_limit",
                    "problems": [f"수정 요청이 {MAX_REVISE}회를 넘었습니다. 직접 고쳐 승인하거나 폐기해 주세요."]}
        feedback = "\n".join(x for x in [state.get("feedback"), d.get("comment", "")] if x)   # 이전 요청도 유지
        return {"status": "revising", "feedback": feedback, "revisions": state.get("revisions", 0) + 1,
                "tries": 0, "problems": [],
                "handoffs": [handoff("approval", "registration_agent", "담당자 수정 요청: " + d.get("comment", ""))]}

    return {"status": "discarded"}


def publish(draft):
    """데이터를 바꾸는 Tool은 approval만 호출한다 (허용 목록)."""
    result = call_tool("approval", "publish_notice", {"notice": draft["notice"], "verify_features": draft["verify"]})
    if not result["ok"]:
        raise RuntimeError(result["text"])
    return {"draft": draft, "status": "published", "notice_id": result["data"]["notice_id"]}
