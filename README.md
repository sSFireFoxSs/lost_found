# 습득물 공고 표준화 멀티에이전트 (PoC)

어느 기관에서 등록하든 같은 품질의 습득물 공고가 올라가도록, **매칭에 필요한 특징은 빠짐없이 채우고 식별 정보는 지키는** 멀티에이전트입니다.
경찰민원24에 추가할 기능을 제안하는 시안이라, 화면은 경찰민원24(KRDS)와 같은 모양으로 만들었습니다.

- 기획: 노션 「습득물 공고 표준화 멀티에이전트」
- 코드 범위: 노션 「에이전트 수업 총정리」의 LangGraph · MCP 내용 안에서만 작성

## 실행

```bash
uv venv --prompt mcp_1 .venv            # 처음 한 번
uv pip install -r requirements.txt       # 처음 한 번
.venv/bin/uvicorn app.main:app --port 8000
```

- 브라우저에서 http://127.0.0.1:8000 (MCP 서버 8801은 웹 서버가 같이 켜고 끕니다)
- 프로젝트 루트의 `.env` 또는 상위 폴더 `.env`에 `OPENAI_API_KEY` 필요. 모델은 `MCP_LAB_MODEL` (기본 `gpt-6-luna`)
- 규칙 테스트(모델 없이): `.venv/bin/python -m tests.test_rules`
- 등록한 데이터는 `data/`에 계속 쌓입니다 (서버를 다시 켜도 유지, git에는 안 올림)
  - `data/jobs.json`: 등록 입력·사진 판독·초안·담당자 결정 이력 (개선용 데이터셋)
  - `data/notices.json`: 게시된 공고 · `data/checkpoints.sqlite`: 그래프 중간 상태(SqliteSaver) · `uploads/`: 사진

## 화면

| 주소 | 화면 | 상태 |
| --- | --- | --- |
| `/` | 습득물 등록 · 작업 목록 · 초안 검토 · 최근 등록된 습득물 | 동작 |
| `/lost_new.html` | 분실물 신고 | UI만 |
| `/lost.html` | 분실물 확인 (내 신고, 매칭 후보) | UI만 (예시 데이터) |
| `/notice.html?id=F-0001` | 상세 보기 (경찰민원24 "분실물 상세 보기" 모양). id 없으면 예시 | 동작 (상세내용은 등록 때 표준 문구로 만든 것) |

## 폴더

```
agents/        LangGraph 에이전트
  categories.py  경찰민원24에서 수집한 정해진 값: 분류, 색상 코드, 상세내용 문구 틀, 물품명 예시, 유형별 템플릿
  state.py       NoticeState, handoff
  nodes.py       registration · verification · privacy · approval 노드
  graph.py       엣지 연결, graph 객체
mcp_server/    MCP
  lost_mcp.py    서버: Resource lost://notices/{id}, Tool publish_notice
  client.py      클라이언트: 가리기(mask), 허용 목록, read_resource, call_tool
app/           FastAPI
  main.py        앱, MCP 서버 켜고 끄기, 라우터·화면 연결
  jobs.py        초안 작업 대기열 (동시 3건, 대기 20건)
  routes/        drafts.py(등록·상태·결정), notices.py(공고 조회)
static/        화면 (style.css, common.js, 페이지 3개)
tests/         모델 없이 도는 규칙 테스트
```

더 자세한 구조는 [ARCHITECTURE.md](ARCHITECTURE.md), 왜 이렇게 했는지는 [DECISIONS.md](DECISIONS.md).

## 아직 안 된 것

- 습득 시각, 유실물 상태(보관중·종결) 관리
- 분실물 신고 저장, 분실물–습득물 매칭(노션 4-3), 본인 확인
