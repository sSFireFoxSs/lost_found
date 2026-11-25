"""그래프 노드들이 함께 쓰는 State와 인계(handoff) 기록."""
import operator
from typing import Annotated, TypedDict


class NoticeState(TypedDict, total=False):
    report: dict                                  # 신고서: date, place, finder_name, category_main, category_sub, memo, checks
    photo_path: str                               # 업로드한 사진 경로
    photo_desc: str                               # VLM 사진 판독 결과 (registration이 처음 한 번 채움)
    item: dict                                    # {"category", "sub_category", "item_name", "features": {항목: 값}}
    problems: list[str]                           # 검증에서 나온 불일치
    to_check: list[str]                           # LLM이 만든 '담당자가 확인할 점'
    tries: int                                    # 검증 실패로 재추출한 횟수
    feedback: str                                 # 담당자의 수정 요청 코멘트
    revisions: int                                # 담당자 수정 요청 횟수
    draft: dict                                   # 공고 초안: public / verify / private / notice
    notice_id: str
    status: str                                   # revising / revise_limit / published / discarded
    handoffs: Annotated[list[dict], operator.add]  # 덮어쓰지 않고 이어 붙임


def handoff(from_agent, to_agent, task):
    return {"from_agent": from_agent, "to_agent": to_agent, "task": task}
