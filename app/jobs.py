"""초안 작업 대기열. LLM 처리는 뒤에서 돌리고, 화면은 thread_id로 상태만 조회한다.

- 동시에 MAX_RUNNING건 처리, 넘치는 건 POOL이 알아서 순서 대기
- 대기 중인 건이 MAX_WAITING을 넘을 때만 새 요청을 막는다
- 승인 대기는 interrupt로 멈춘 State가 checkpointer에 저장될 뿐이라 일꾼을 차지하지 않는다
"""
import time
from concurrent.futures import ThreadPoolExecutor

from agents.graph import graph

MAX_RUNNING = 5                                   # 동시에 돌리는 초안 작업 수 (초안 값, 평가 후 조정)
MAX_WAITING = 100                                  # 대기열 상한. 넘으면 "잠시 후 다시"
POOL = ThreadPoolExecutor(max_workers=MAX_RUNNING)
JOBS: dict[str, dict] = {}                        # thread_id → 상태 (서버 메모리)
# shortcut: JOBS·checkpointer가 메모리에 있어 서버를 다시 켜면 사라짐. 운영 전 DB(예: SqliteSaver)로 교체.


def is_full() -> bool:
    return sum(j["status"] == "queued" for j in list(JOBS.values())) >= MAX_WAITING   # list(): 다른 스레드가 추가해도 안전


def submit(thread_id: str, graph_input, **meta):
    """작업을 대기열에 넣고 바로 돌아온다. graph_input은 처음 State 또는 Command(resume=...)."""
    job = JOBS.setdefault(thread_id, {"thread_id": thread_id, "created": time.time()})
    job.update(meta, status="queued", error=None)
    POOL.submit(run, thread_id, graph_input)


def run(thread_id: str, graph_input):
    job = JOBS[thread_id]
    job["status"] = "running"
    try:
        out = graph.invoke(graph_input, {"configurable": {"thread_id": thread_id}})
        hs = out.get("handoffs", [])                # 실제로 일을 넘긴 순서 (재추출은 registration_agent가 반복)
        job["path"] = [h["from_agent"] for h in hs] + [hs[-1]["to_agent"]] if hs else []
        job["item"] = out.get("item")
        if "__interrupt__" in out:                # 담당자 결정을 기다리며 멈춤
            job.update(out["__interrupt__"][0].value, status="waiting_approval")
        else:                                     # published / discarded
            job.update(status=out["status"], notice_id=out.get("notice_id"), draft=out.get("draft"))
    except Exception as e:                        # 실패해도 다른 작업은 계속 돈다
        job.update(status="failed", error=f"{type(e).__name__}: {e}")
