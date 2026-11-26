"""초안 작업 대기열. LLM 처리는 뒤에서 돌리고, 화면은 thread_id로 상태만 조회한다.

- 동시에 MAX_RUNNING건 처리, 넘치는 건 POOL이 알아서 순서 대기
- 대기 중인 건이 MAX_WAITING을 넘을 때만 새 요청을 막는다
- 승인 대기는 interrupt로 멈춘 State가 checkpointer(SQLite)에 저장될 뿐이라 일꾼을 차지하지 않는다
- 작업 기록은 data/jobs.json에 계속 쌓인다 (등록 입력, 사진 판독, 초안, 담당자 결정 이력 → 개선용 데이터셋)
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agents.graph import graph

MAX_RUNNING = 5                                   # 동시에 돌리는 초안 작업 수 (초안 값, 평가 후 조정)
MAX_WAITING = 100                                  # 대기열 상한. 넘으면 "잠시 후 다시"
POOL = ThreadPoolExecutor(max_workers=MAX_RUNNING)
JOBS_FILE = Path("data/jobs.json")
LOCK = threading.Lock()                           # 여러 스레드가 동시에 파일을 쓰지 않게


def load() -> dict[str, dict]:
    if not JOBS_FILE.exists():
        return {}
    jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    for job in jobs.values():                     # 처리 도중 서버가 꺼진 건은 실패로 표시 (승인 대기 건은 그대로 이어 감)
        if job["status"] in ("queued", "running"):
            job.update(status="failed", error="서버 재시작으로 처리가 중단됐습니다. 다시 등록해 주세요.")
    return jobs


JOBS: dict[str, dict] = load()                    # thread_id → 상태


def update(thread_id: str, **fields) -> dict:
    """작업 상태를 바꾸고 바로 파일에 쓴다. 바꾸기와 쓰기를 한 잠금 안에서 해 스레드끼리 꼬이지 않게."""
    with LOCK:
        job = JOBS.setdefault(thread_id, {"thread_id": thread_id, "created": time.time(), "history": []})
        job.update(fields)
        JOBS_FILE.parent.mkdir(exist_ok=True)
        tmp = JOBS_FILE.with_suffix(".tmp")       # 다 쓴 뒤 바꿔치기: 쓰다 꺼져도 기존 파일이 깨지지 않음
        tmp.write_text(json.dumps(JOBS, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(JOBS_FILE)
        return job


def is_full() -> bool:
    return sum(j["status"] == "queued" for j in list(JOBS.values())) >= MAX_WAITING   # list(): 다른 스레드가 추가해도 안전


def submit(thread_id: str, graph_input, **meta):
    """작업을 대기열에 넣고 바로 돌아온다. graph_input은 처음 State 또는 Command(resume=...)."""
    update(thread_id, **meta, status="queued", error=None)
    POOL.submit(run, thread_id, graph_input)


def add_history(thread_id: str, decision: dict):
    """담당자 결정 이력. 무엇을 고쳤는지가 개선에 가장 쓸모 있는 데이터."""
    update(thread_id, history=JOBS[thread_id].get("history", []) + [{"at": time.time(), **decision}])


def run(thread_id: str, graph_input):
    update(thread_id, status="running")
    try:
        out = graph.invoke(graph_input, {"configurable": {"thread_id": thread_id}})
        hs = out.get("handoffs", [])                # 실제로 일을 넘긴 순서 (재추출은 registration_agent가 반복)
        path = [h["from_agent"] for h in hs] + [hs[-1]["to_agent"]] if hs else []
        if "__interrupt__" in out:                # 담당자 결정을 기다리며 멈춤
            update(thread_id, **out["__interrupt__"][0].value, path=path, item=out.get("item"), status="waiting_approval")
        else:                                     # published / discarded
            update(thread_id, path=path, item=out.get("item"), status=out["status"],
                   notice_id=out.get("notice_id"), draft=out.get("draft"))
    except Exception as e:                        # 실패해도 다른 작업은 계속 돈다
        update(thread_id, status="failed", error=f"{type(e).__name__}: {e}")
