"""습득물 공고 표준화 멀티에이전트 그래프 (LangGraph).

registration → verification ─(불일치, 재시도 남음)→ registration
                            └→ privacy → approval(interrupt, 담당자 결정)
                                          ├ 승인 / 수정후승인 → 게시 → END
                                          ├ 수정요청 → registration (상한 넘으면 approval에서 다시 대기)
                                          └ 폐기 → END
"""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from agents.nodes import approval, privacy, registration, verification
from agents.state import NoticeState

MAX_RETRY = 2                                     # 검증 실패 시 재추출 상한 (노션: 초안 값)


def after_verification(state):
    if not state["problems"]:
        return "privacy"
    return "registration" if state["tries"] <= MAX_RETRY else "privacy"   # 상한 넘으면 문제를 달고 담당자에게


def after_approval(state):
    return {"revising": "registration", "revise_limit": "approval"}.get(state["status"], "end")


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
    builder.add_conditional_edges("approval", after_approval,
                                  {"registration": "registration", "approval": "approval", "end": END})
    return builder.compile(checkpointer=checkpointer)


graph = assemble(InMemorySaver())                 # interrupt에는 checkpointer + thread_id가 필수
