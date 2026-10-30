# AgentFlow — Project Rules & Guidelines

## Project Overview

AgentFlow là một **Competitive Intelligence platform** xây trên nền agent execution engine (Supervisor-Worker + deterministic dispatcher). Vertical slice hiện tại: user định nghĩa watchlist (sản phẩm của mình + đối thủ + official sources được duyệt) → approve revision → scheduled/manual run → researcher fetch → snapshot capture/normalize → deterministic section diff → coordinator rounds do code điều phối (vai researcher/verifier/analyst, tối đa 20 tasks/round) → baseline CAS promotion → typed brief + Markdown download.

Giao diện có hai tab chính: **Chat** (đề xuất setup watchlist + hỏi đáp lịch sử) và **Intelligence** (watchlist / changes / briefs / timeline / schedules), kèm in-app notifications và lịch chạy weekly/daily.

## Tech Stack

| Layer      | Technology                         |
| ---------- | ---------------------------------- |
| Backend    | Python, FastAPI                    |
| Frontend   | React (Vite, proxy `/api` → `localhost:8001`; Compose expose 8000) |
| Database   | PostgreSQL, Redis (MongoDB optional) |
| Container  | Docker, Docker Compose             |
| CI/CD      | GitHub Actions                     |
| AI/Agent   | LangChain, LangGraph               |
| Validation | Pydantic                           |
| Frontend Tests | Vitest + Testing Library + jsdom |
| Browser Fetch | Playwright Chromium (opt-out flag `AGENTFLOW_CRAWLER_BROWSER_ENABLED`) |
| Scheduling | `tzdata` (IANA database cho schedule timezones) |
| Evaluation | Structured eval scripts (`scripts/run_ci_evaluation.py`, `scripts/benchmark_ollama.py`, `evaluation/`) |

### Data Ownership

| Data Entity / Scope | Database | Rationale |
| --- | --- | --- |
| Users, Flow Definitions, Agent & Tool Catalog | **PostgreSQL** | Relational, strict schemas, ACID transactions |
| Conversations, Workflow Versions, Runs, Replayable Events, Evidence, Artifact Metadata | **PostgreSQL** | Durable source of truth; not MongoDB |
| Snapshots, Comparisons, Change Candidates, Investigation Rounds, Briefs, Schedules, Notifications | **PostgreSQL** | CI domain state; durability requirements giống runs/evidence |
| Report/chart files | **Filesystem** | Scoped artifact storage; PostgreSQL holds ownership and references |
| Snapshot/Brief text blobs | **Filesystem** (`ARTIFACT_ROOT`) | Payload lớn nằm ngoài DB; PostgreSQL giữ URI + content hash |
| Queue and realtime fan-out; future rate-limit/cache counters | **Redis** | Coordination only; not authoritative run/conversation state |

## Project Structure

```
agentflow/
├── backend/
│   ├── alembic/versions/           # DB migrations 0001–0011
│   └── app/
│       ├── main.py                  # FastAPI entry point
│       ├── api/v1/                  # REST routers: health, auth, flows, catalog,
│       │                            #   conversations, runs, workflows, watchlists, ci_reads
│       ├── core/                    # App settings & core config
│       ├── db/                      # Postgres client, init_db & catalog seeding
│       ├── execution/               # Core agent execution engine
│       │   ├── agents/              # base (Supervisor/Worker runtime), registry, resolver (role profiles)
│       │   ├── nodes/               # TaskDispatcher (code-based DAG router)
│       │   ├── tools/               # ToolRegistry (base.py) + tools, crawler/ (orchestrator + browser_fetcher), cache, tool_runner
│       │   ├── research_*.py        # Research contracts/evidence/http/reduction/validation/coverage/presentation
│       │   ├── graph.py, state.py, llm.py, model_router.py
│       │   ├── run_budget.py, context_budget.py
│       │   └── checkpointer.py, ports.py, adapters.py
│       ├── modules/                 # Business logic modules
│       │   ├── competitive_intelligence/  # watchlist, snapshots, investigation/coordinator,
│       │   │                            #   briefs, scheduling, timeline, history_qa, evaluation...
│       │   ├── agent_catalog/, catalog/, conversations/, flows/,
│       │   │                            #   identity/, results/, runs/, system/, workflows/
│       ├── infrastructure/          # container, health_probe, ci_setup_planner
│       │   ├── artifacts/           # storage, evidence_store, snapshot_blobs, brief_blobs
│       │   ├── postgres/            # repositories + models (incl. intelligence_*)
│       │   ├── redis/               # run_queue, event publishers, rate_limiter
│       │   └── mongodb/             # Legacy run repository (disabled by default)
│       ├── shared/                  # errors, ids, validation, metrics, observability, artifact_paths...
│       └── workers/                 # Background workers: run_worker, schedule_worker
├── frontend/                        # React (Vite); src/{components, services incl. ciApi} + vitest tests
├── tests/                           # Unit suite (python scripts/run_tests.py unit)
├── integration_tests/               # Real PostgreSQL/Redis suite (see its README)
├── evaluation/                      # Eval cases (cases/*.json), ci_scenarios.json, metric helpers
├── scripts/                         # run_tests, run_ci_evaluation, evaluation_runs, benchmark_ollama, summarize_http_metrics
├── docs/                            # Documentation
├── docker-compose.yml               # Docker orchestration
└── requirements.txt                 # Python dependencies
```

## Architecture

### Two-Phase Execution Model (Supervisor-Worker + Task Dispatcher)

Hệ thống tách biệt rõ ràng giữa hai giai đoạn:

1. **Build Phase (Planner / Flow Designer)**:
   - **SupervisorAgent**: Giao tiếp với người dùng, phân tích intent, tạo kế hoạch (`FlowDefinition` chứa các `Task` với dependencies).
   - Kết thúc với bước review/approval của người dùng trước khi thực thi.

2. **Run Phase (Deterministic Execution Engine)**:
   - **ExecutionManager / TaskDispatcher**: Node điều phối bằng code (code-based router) đọc `plan`, lọc ra các `Task` có status `pending` đã thỏa mãn `dependencies`, và gán `current_task` cho WorkerAgent tương ứng.
   - **WorkerAgent**: Thực thi task bằng ReAct loop (LLM + tool calls). Kết quả tool output được bao bọc trong tag `<tool_output>...</tool_output>` để chống prompt injection.
   - Dispatcher hiện chọn task tuần tự theo DAG, **không gọi lại Supervisor LLM sau mỗi bước worker**. Bounded parallel DAG execution là mục tiêu; tool crawl batch đã có concurrency riêng.

### Competitive Intelligence Pipeline (Vertical Slice)

CI chạy trên engine ở trên với pipeline code-owned:

1. **Watchlist & revision**: User khai báo own product + competitors + official sources được duyệt; thay đổi cấu hình phải qua approve revision trước khi run.
2. **Run trigger**: `schedule_worker` (weekly/daily) hoặc manual run.
3. **Snapshot & diff**: Researcher (`source_researcher`) fetch các nguồn được duyệt → snapshot capture/normalize → deterministic section diff → change candidates.
4. **Investigation rounds (coordinator-as-code)**: `investigation_coordinator` **không gọi model nào**. Nó biến candidates + câu hỏi chưa cover thành round tasks có bounds (vai `source_researcher` / `evidence_verifier` / `competitive_analyst` qua `CI_ROLE_TOOLS`, tối đa `MAX_ROUND_TASKS = 20` tasks/round), reject đề xuất out-of-scope / over-budget / cyclic, và reconstruct round state sau restart.
5. **Baseline CAS promotion**: Candidate được xác nhận → snapshot promote làm baseline so sánh cho các lần sau.
6. **Brief & delivery**: Sinh typed brief + Markdown download; in-app notifications. Outbound delivery và release gate cuối vẫn là human-gated.

Run được quan sát qua observer ports `TaskCompletedObserver` / `RunCompletedObserver` (`modules/runs/observers.py`); observer failures được **contained** — không làm hỏng run chính.

### State Management

State được quản lý qua `State` TypedDict (`total=False`) với các reducer functions:
- `add_messages`: Giữ tối đa 10 messages gần nhất (sliding window).
- `add_logs`: Hỗ trợ lưu trữ structured `LogEntry` hoặc string logs.
- `add_results`: Append kết quả thực thi; dùng chung cho `execution_timings`, `llm_call_metrics`, `task_execution_metrics`.
- `update_plan`: Merge tasks theo `id` (upsert); hỗ trợ replace toàn bộ plan qua dict `{"__replace__": True, "tasks": [...]}`.

Các trường domain-specific được đưa vào `metadata: Dict[str, Any]` thay vì nằm trực tiếp ở root level của core `State`.

### Registry Pattern

Cả Agent và Tool đều sử dụng Registry pattern:
- `AgentRegistry` (`execution/agents/registry.py`): đăng ký agent **runtime class** bằng decorator `@AgentRegistry.register("name")`. Runtime chỉ có hai class: `supervisor` và `worker`.
- Các vai cụ thể (worker, source_researcher, synthesis_agent, report_agent, chart_agent, product_analyst, evidence_verifier, competitive_analyst) là **prompt + tool profiles**, không phải class riêng: định nghĩa trong `DEFAULT_AGENT_PROFILES` (`execution/agents/resolver.py`) và được seed vào agent catalog bởi `seed_defaults()` (`db/init_db.py`). Role mới phải thêm ở cả hai nơi.
- `ToolRegistry` (`execution/tools/base.py`): Đăng ký tool bằng decorator `@ToolRegistry.register_tool(name="name")` kết hợp với `@tool()` của LangChain; autodiscovery qua `execution/tools/registry.py`.

## Cost, Security & Guardrails

- **Prompt Injection Defense**: Tất cả kết quả thực thi tool từ bên ngoài (HTTP, file read, web search) được bao bọc trong các tag `<tool_output>...</tool_output>` kèm chỉ thị system prompt để LLM phân biệt dữ liệu thô và mệnh lệnh gốc.
- **Dynamic Task Limits**: Cho phép cấu hình `max_iterations` và `timeout_seconds` cho từng Task/WorkerAgent thay vì hardcode.
- **Token & Cost Budget Guardrails**: Run-level budget được enforce bằng `RunBudget` (`execution/run_budget.py`): max inference calls, estimated tokens (heuristic utf8/2 input + reserved output — **admission estimate, không phải provider usage**), wall-clock, kèm cooperative cancellation. CI investigation có `InvestigationBudget` caps riêng. Không coi việc có setting là đã có guardrail — per-task timeout/iteration và budget controls cần được test riêng.
- **Tool Authorization**: Worker Agent chỉ được phép truy cập các tools nằm trong whitelist đăng ký của agent đó.

## Coding Conventions

### General

- **Ngôn ngữ code**: Tất cả code, variable names, function names, class names viết bằng **tiếng Anh**.
- **Comments & docstrings**: Có thể viết bằng tiếng Việt hoặc tiếng Anh, nhưng ưu tiên tiếng Anh cho docstrings công khai.
- **Type hints**: Bắt buộc cho tất cả function signatures. Sử dụng `typing` và `Annotated` types.
- **Async/await**: Tất cả agent execution methods phải là `async`. Sử dụng `ainvoke` thay vì `invoke` khi có thể.
- **Pydantic models**: Sử dụng Pydantic `BaseModel` cho tất cả structured data (schemas, DTOs, configs).

### Python Style

- Tuân theo PEP 8.
- Sử dụng `snake_case` cho functions và variables.
- Sử dụng `PascalCase` cho classes.
- Sử dụng `UPPER_SNAKE_CASE` cho constants.
- Tối đa 120 ký tự mỗi dòng.
- Import order: stdlib → third-party → local (tách bằng blank line).

### Error Handling

- Sử dụng `try/except` blocks trong tool execution và agent loops.
- Log errors vào `logs` field trong State, không swallow errors silently.
- Trả về error message có ý nghĩa thay vì raise exception trong tool implementations.

## Adding New Components

Quy trình thêm Tool / Agent / API Endpoint được định nghĩa dưới dạng Workflow (chuỗi bước lặp lại), chạy qua slash command thay vì lặp lại thủ công:

- `/add-tool` → `.agent/workflows/add-tool.md`
- `/add-agent` → `.agent/workflows/add-agent.md`
- `/add-api-endpoint` → `.agent/workflows/add-api-endpoint.md`

Agent roles là prompt + tool profiles, không phải runtime class. Khi thêm role mới, phải thêm profile vào `DEFAULT_AGENT_PROFILES` (`execution/agents/resolver.py`) **và** seed vào agent catalog trong `seed_defaults()` (`db/init_db.py`); chỉ thêm class mới vào `AgentRegistry` khi thật sự cần runtime behavior mới (hiện chỉ có `supervisor` và `worker`).

## State Schema Reference

```python
class LogEntry(BaseModel):
    timestamp: str
    level: Literal["INFO", "WARNING", "ERROR", "DEBUG"] = "INFO"
    node: str
    run_id: Optional[str] = None
    message: str

class Task(BaseModel):
    id: int
    task_key: Optional[str] = None           # Stable definition identity; id is runtime ordering only
    task_execution_id: Optional[str] = None
    node: str
    agent_id: Optional[str] = None
    capability: Optional[str] = None
    tool_names: List[str] = []
    tool_ids: List[str] = []
    status: Literal["done", "partial", "pending", "running", "failed", "skipped", "interrupted"]
    error: Optional[str] = None
    description: str
    dependencies: List[int] = []
    timeout_seconds: Optional[int] = None
    max_iterations: Optional[int] = None
    expected_output_type: str = "raw_data"
    input_mapping: Dict[str, Any] = {}
    config: Dict[str, Any] = {}
    research_requirements: List[ResearchRequirement] = []  # max 64; từ app.execution.research_contracts

class FlowDefinition(BaseModel):
    flow_id: str
    name: str
    tasks: List[Task]
    metadata: Dict[str, Any] = {}

class State(TypedDict, total=False):
    messages: Annotated[list[BaseMessage | str], add_messages]           # Chat history (max 10)
    plan: Annotated[list[Task], update_plan]                             # Execution plan (DAG tasks)
    current_task: Optional[Task]                                         # Task đang/sắp thực thi
    logs: Annotated[list[Any], add_logs]                                 # Structured LogEntry or string logs
    result_storage: Annotated[list, add_results]                         # Task results
    execution_timings: Annotated[list[Dict[str, Any]], add_results]      # Step timing metrics
    llm_call_metrics: Annotated[list[Dict[str, Any]], add_results]       # LLM call metrics
    task_execution_metrics: Annotated[list[Dict[str, Any]], add_results] # Task execution metrics
    mode: Literal["conversation", "executing"]                           # Operation mode
    metadata: Optional[Dict[str, Any]]                                   # Domain-specific extension data
```

Khi cập nhật State, **chỉ trả về các fields cần thay đổi** trong dict return. Reducers sẽ tự động merge.

## Testing

- Framework: `unittest` với `IsolatedAsyncioTestCase` cho async tests.
- Mock LLM: Sử dụng `unittest.mock.AsyncMock` và `MagicMock` cho `BaseChatModel`.
- Unit suite: `python scripts/run_tests.py unit` (isolated workspace, mocked HTTP/DNS, no live model).
- Legacy discovery remains available: `python -m unittest discover tests/`; it does not install the suite-level network guard.
- Real PostgreSQL/Redis suite: `python scripts/run_tests.py integration`; see `integration_tests/README.md` for dedicated targets and required safety flags. Never point it at development data.
- CI evaluation replay: `python scripts/run_ci_evaluation.py` — deterministic (no network, no model, no clock dependence); exit non-zero khi có fixture mismatch, dùng làm gate; reports ghi dưới `workspace_data/evaluation/ci/`.
- Local LLM benchmark: `python scripts/benchmark_ollama.py` — private, cần endpoint Ollama live; kết quả JSON không commit.
- Frontend unit tests: `npm test` (= `vitest run`) trong `frontend/` (Vitest + Testing Library + jsdom; setup ở `src/test-setup.js`).
- Every test that changes environment variables must restore them. File-producing tests must use a temporary workspace; never remove the repository's `workspace_data`.
- Test path setup: Tests thêm `backend/` vào `sys.path` để import `app.*` modules.
- Mọi tool và agent mới **phải có unit test** trước khi merge.

## Development Workflow

### Git & Branching

- `dev` là nhánh tích hợp chính; `main` chỉ chứa code đã release, luôn ở trạng thái production-ready.
- Luôn `git pull origin dev` trước khi tạo branch mới; không code trực tiếp trên `dev` hay `main`.
- Tên branch: `feature/<mo-ta>`, `fix/<mo-ta>`, `chore/<mo-ta>` — tạo từ `dev`. Gắn số issue khi có, ví dụ `fix/123-token-limit`.
- `hotfix/<mo-ta>` là ngoại lệ: tạo thẳng từ `main` khi cần vá gấp lỗi production.

### Commit

- Conventional Commits: `<type>(<scope>): <mô tả>`, `type` ∈ {feat, fix, refactor, docs, test, chore, perf}.
- `scope` nên khớp module bị đổi: `execution`, `api`, `tools`, `agents`, `frontend`, `state`...
- Mỗi commit là một thay đổi logic độc lập; không gộp nhiều việc không liên quan vào một commit.

### Trước khi mở Pull Request

- Chạy `python -m unittest discover tests/` và lint, đảm bảo pass ở local.
- Tool/Agent mới bắt buộc có unit test tương ứng (xem mục Testing) — không mở PR nếu thiếu test.
- Rebase lên `dev` mới nhất (hoặc `main` nếu là hotfix), giải quyết conflict trước khi tạo PR.
- Xoá code debug (`print`, `console.log`), comment thừa trước khi push.

### Pull Request & CI

- PR nhắm vào `dev`; riêng `hotfix/*` nhắm thẳng vào `main`.
- Mô tả PR nêu rõ: thay đổi gì, vì sao, cách test, issue liên quan (`Closes #...`).
- PR nhỏ, tập trung một mục đích — tách riêng nếu đổi cả backend lẫn frontend cho cùng một thay đổi lớn.
- GitHub Actions CI (test, lint) phải pass; không merge khi CI đỏ.
- Không tự merge PR của chính mình trừ khi được team cho phép rõ ràng.

### Merge & Release

- Squash and merge feature/fix/chore vào `dev`; xoá branch sau khi merge.
- Định kỳ theo release, mở PR từ `dev` → `main`; tag version sau khi merge vào `main`.
- Hotfix sau khi merge vào `main` phải được back-merge (merge hoặc cherry-pick) lại vào `dev` để không bị mất fix ở lần release tiếp theo.
- Không `force push` lên `dev` hoặc `main`. Cần rollback thì dùng `git revert`, không xoá lịch sử.

- **Quy trình Git & Verification Bắt Buộc**: Mỗi khi hoàn thành hoặc chuyển tiếp một tính năng mới, Agent BẮT BUỘC phải kiểm tra `git status` đầy đủ, chạy 100% unit tests pass, và tuân thủ đúng quy trình Git Flow (Feature Branch -> Commit -> Push -> Merge dev -> Checkout/Pull dev).
- **Không commit** `.venv/`, `__pycache__/`, hay `docs/` (đã trong `.gitignore`).
- **Không hardcode** API keys hay secrets. Sử dụng environment variables.
- **File tools** sử dụng `workspace_data/` directory để sandbox file I/O, tránh path traversal.
- **Worker agent** mặc định tối đa 5 iterations trong tool loop (hoặc theo cấu hình task) để tránh infinite loops.
- **Messages** được giữ tối đa 10 messages gần nhất để kiểm soát context window.
