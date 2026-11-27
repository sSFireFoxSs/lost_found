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

## 카카오톡 분실물 접수 서버

카카오는 메시지 전달과 말풍선 표시만 맡습니다. 대화 내용은 Python에서 처리하며,
기존 습득물 웹 앱과 독립적으로 `8001` 포트에서 실행합니다. 현재 구현은 **텍스트 접수**입니다.
사진 입력, 습득물 매칭, 경찰 시스템에 실제 신고하는 연동은 포함하지 않습니다.

### 코드 읽는 순서

1. [`app/kakao.py`](app/kakao.py)의 `receive_message()` — 카카오 JSON에서 사용자와 메시지를 꺼냅니다.
2. 같은 파일의 `process_event()` — 응답을 먼저 반환한 뒤 대화를 처리합니다.
3. [`agents/lost_schema.py`](agents/lost_schema.py)의 `LostReport` — 필수/선택 항목, 값의 형식, 누락 시 질문을 정의합니다.
4. [`agents/lost_intake.py`](agents/lost_intake.py)의 `build_graph()` — LangGraph의 노드와 조건부 분기를 연결합니다.
5. 같은 파일의 `extract_fields()` → `validate_schema()` → `ask_missing()` / `summarize()` — LangChain으로 추출한 뒤 스키마 검사 결과에 따라 재질문하거나 요약합니다.
6. [`app/intake_store.py`](app/intake_store.py)의 `complete()` — 스키마를 다시 검사하고 확인된 신고와 대화 상태를 함께 저장합니다.
7. `kakao_response()` / `deliver_callback()` — 결과를 카카오 말풍선으로 변환하고 전달합니다.

예: `어제 강남역에서 검은 지갑을 잃어버렸어요` → 누락된 특징 질문 → 요약 → **접수하기** → 접수번호.
LangChain의 `with_structured_output()`이 자연어에서 값을 추출하고 LangGraph가 다음 흐름으로 처리합니다.

```text
메시지 → 시작/취소/확인 분기 → 정보 추출 → 기존 값과 병합 → 스키마 검사
                                                        ├─ 누락/오류 → 해당 항목 질문
                                                        ├─ 입력 완료 → 요약·확인 버튼
                                                        └─ 유효한 확인 버튼 → 접수 확정
```

한 메시지에 여러 값이 있으면 한꺼번에 반영하고, 짧은 답변은 직전 질문과 함께 해석합니다.
필수 응답은 물품·분실 시점·분실 장소·색상·외관 특징·본인확인용 특징이며, 분류·브랜드는 선택입니다.
물품 종류 외의 필수 응답은 사용자가 명시적으로 `모름`/`없음`이라고 답할 수 있습니다. AI가 빈칸을 추측해서 채우지는 않도록 지시합니다.
필수 여부는 `LostReport`의 기본값 유무로 정하며, 부분 추출용 `ExtractedFields`도 이 스키마에서 생성합니다.
누락 항목 검사·확인 버튼 검증·중복 접수 방지는 코드가 담당합니다.

추가 질문을 반환하면 이번 그래프 실행은 종료합니다. 다음 카카오 메시지가 오면 PostgreSQL에서 저장된 상태를
읽어 다시 `invoke()`하므로 대화가 이어집니다. `interrupt()`나 LangGraph 체크포인터는 사용하지 않습니다.
대화 상태와 신고를 하나의 DB 트랜잭션으로 저장하는 기존 구조를 유지합니다.
기존 습득물 앱의 저장 방식은 main 코드 그대로입니다.

### 로컬 실행 (PowerShell)

```powershell
uv pip install -r requirements.txt
docker compose up -d --wait
# env.example을 참고해 .env 설정
.venv\Scripts\python.exe -m uvicorn app.kakao:app --host 127.0.0.1 --port 8001
```

- `DATABASE_URL`: 위 Compose용 연결 문자열은 `env.example`에 있습니다.
- `OPENAI_API_KEY`, `MCP_LAB_MODEL`: AI 정보 추출에 사용합니다.
- `KAKAO_SKILL_SECRET`: 직접 생성한 충분히 긴 임의 문자열. 아래 스킬 헤더와 같아야 합니다.
- `KAKAO_BOT_ID`: 챗봇 설정 화면의 봇 ID. 설정하면 다른 봇의 요청을 거절합니다.
- `.env`와 키는 Git에 올리지 않습니다. 서버는 `--workers 1`로 실행합니다.

### 카카오 관리자센터 설정

1. **스킬 → 분실물 접수**의 URL에 `https://터널주소/kakao/skill`을 입력합니다.
2. 같은 화면의 **헤더값 입력**에 Key `X-Skill-Key`, Value는 `.env`의 `KAKAO_SKILL_SECRET` 값을 입력합니다.
3. **시나리오 → 폴백 블록**에서 이 스킬을 선택하고, 봇 응답을 **스킬데이터 사용**으로 저장합니다.
4. 블록의 **더보기 → Callback 설정**을 켭니다. 카카오가 보낸 콜백 주소로 AI 결과를 돌려줍니다.
5. 개발 채널에서 `분실물 접수` 또는 분실한 상황을 보냅니다. 운영 채널은 블록을 배포한 뒤 사용합니다.

콜백을 켜기 전에도 `결과 확인` 버튼으로 처리 결과를 볼 수 있습니다. 콜백 전달이 실패한 경우에도
`결과 확인`을 입력하면 마지막 결과를 다시 보여줍니다. 새 메시지 처리 중에는 다음 메시지를 접수하지 않고
결과 확인 후 다시 보내도록 안내합니다. 서버 재시작 시 처리 중이던 요청은 실패 표시되지만 이전 입력은 유지됩니다.
현재는 개발용 단일 프로세스 구성으로, 콜백 자동 재시도나 영속 작업 큐는 없습니다.

`lost_reports`는 사용자 확인이 끝난 신고, `kakao_intake_sessions`는 진행 중 대화,
`kakao_intake_events`는 요청·응답·콜백 전송 상태를 저장합니다. 신고 조회를 공개 HTTP API로 제공하지 않습니다.

### 검증

```powershell
$env:TEST_DATABASE_URL = 'postgresql://lost_found:lost_found_dev@127.0.0.1:5433/lost_found'
.venv\Scripts\python.exe -m unittest discover -s tests -p test_kakao_intake.py -v
```

AI 호출은 가짜 추출기로 대체합니다. PostgreSQL 테스트는 임시 스키마를 사용하고 종료 시 해당 스키마만 제거합니다.
그래프 분기, 여러 메시지의 값 병합, 누락/잘못된 값 재질문, 수정 후 확인, 중복 방지,
미완료 대화의 재시작 복구와 저장 직전 스키마 검사를 검증합니다.
개발 서버가 같은 DB에서 실행 중이면 먼저 종료한 뒤 테스트합니다(단일 서버 잠금).
실제 카카오 대화 확인은 개발 채널에서 별도로 진행합니다.

참고: [카카오 스킬 요청·응답 규격](https://kakaobusiness.gitbook.io/main/tool/chatbot/skill_guide/answer_json_format),
[카카오 콜백 설정](https://kakaobusiness.gitbook.io/main/tool/chatbot/skill_guide/ai_chatbot_callback_guide).

## 화면

| 주소 | 화면 | 상태 |
| --- | --- | --- |
| `/` | 습득물 등록 · 작업 목록 · 초안 검토 · 최근 등록된 습득물 | 동작 |
| `/lost_new.html` | 분실물 신고 | UI만 |
| `/lost.html` | 분실물 확인 (내 신고, 매칭 후보) | UI만 (예시 데이터) |
| `/notice.html?id=F-0001` | 상세 보기 (경찰민원24 "분실물 상세 보기" 모양). id 없으면 예시 | 공고 읽기는 동작, 상세내용 문구는 UI 시안 |

## 폴더

```
agents/        LangGraph 에이전트
  categories.py  대분류·소분류(경찰민원24 기준), 유형별 템플릿, 체크 사항
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

- 상세내용 문구를 에이전트가 쓰는 기능 (지금은 화면에서 표준 문구에 공개 특징을 끼워 넣어 보여 줌), 접수기관·보관장소·연락처·습득 시각 입력
- 분실물 신고 저장, 분실물–습득물 매칭(노션 4-3), 본인 확인
- 저장소: 작업·공고·체크포인트가 모두 메모리에 있어 서버를 다시 켜면 사라짐
