# AgentFlow — Project Rules & Guidelines

## Project Overview

AgentFlow là một AI Agent Platform cho phép người dùng tạo, quản lý và thực thi các workflow dựa trên LLM agents. Hệ thống sử dụng kiến trúc Supervisor-Worker, trong đó Supervisor phân tích yêu cầu và lập kế hoạch, còn các Worker thực thi từng task cụ thể với tool support.

## Tech Stack

| Layer      | Technology                         |
| ---------- | ---------------------------------- |
| Backend    | Python, FastAPI                    |
| Frontend   | React                              |
| Database   | PostgreSQL, Redis (MongoDB optional) |
| Container  | Docker, Docker Compose             |
| CI/CD      | GitHub Actions                     |
| AI/Agent   | LangChain, LangGraph               |
| Validation | Pydantic                           |

### Data Ownership

| Data Entity / Scope | Database | Rationale |
| --- | --- | --- |
| Users, Flow Definitions, Agent & Tool Catalog | **PostgreSQL** | Relational, strict schemas, ACID transactions |
| Conversations, Workflow Versions, Runs, Replayable Events, Evidence, Artifact Metadata | **PostgreSQL** | Durable source of truth; not MongoDB |
| Report/chart files | **Filesystem** | Scoped artifact storage; PostgreSQL holds ownership and references |
| Queue and realtime fan-out; future rate-limit/cache counters | **Redis** | Coordination only; not authoritative run/conversation state |

## Project Structure

```
agentflow/
├── backend/
│   └── app/
│       ├── main.py                  # FastAPI entry point
│       ├── api/                     # REST API routes (FastAPI routers)
│       ├── execution/               # Core agent execution engine
│       │   ├── agents/              # Agent definitions (base, registry)
│       │   ├── nodes/               # LangGraph node & dispatcher definitions
│       │   ├── tools/               # Tool implementations & registry
│       │   ├── state.py             # Shared State (TypedDict + reducers)
│       │   └── context.py           # Execution context
│       ├── modules/                 # Business logic modules
│       │   ├── agent_catalog/       # Agent catalog management
│       │   ├── flows/               # Flow definitions & service
│       │   └── runs/                # Run history & tracking
│       └── workers/                 # Background workers
├── frontend/                        # React frontend
├── tests/                           # Unit & integration tests
├── docs/                            # Documentation
├── docker-compose.yml               # Docker orchestration
└── requirements.txt                 # Python dependencies
```

## Architecture

### Two-Phase Execution Model (Supervisor-Worker + Task Dispatcher)

Hệ thống tách biệt rõ ràng giữa hai giai đoạn:

1. **Build Phase (Planner / Flow Designer)**:
   - **SupervisorAgent**: Giao tiếp với người dùng, phân tích intent, tạo kế hoạch (`FlowDefinition` chứa các `Task` với dependencies).
   - Kết thúc với buớc review/approval của người dùng trước khi thực thi.

2. **Run Phase (Deterministic Execution Engine)**:
   - **ExecutionManager / TaskDispatcher**: Node điều phối bằng code (code-based router) đọc `plan`, lọc ra các `Task` có status `pending` đã thỏa mãn `dependencies`, và gán `current_task` cho WorkerAgent tương ứng.
   - **WorkerAgent**: Thực thi task bằng ReAct loop (LLM + tool calls). Kết quả tool output được bao bọc trong tag `<tool_output>...</tool_output>` để chống prompt injection.
   - Dispatcher hiện chọn task tuần tự theo DAG, **không gọi lại Supervisor LLM sau mỗi bước worker**. Bounded parallel DAG execution là mục tiêu; tool crawl batch đã có concurrency riêng.

### State Management

State được quản lý qua `State` TypedDict với các reducer functions:
- `add_messages`: Giữ tối đa 10 messages gần nhất (sliding window).
- `add_logs`: Hỗ trợ lưu trữ structured `LogEntry` hoặc string logs.
- `add_results`: Append kết quả thực thi.
- `update_plan`: Merge tasks theo `id` (upsert).

Các trường domain-specific được đưa vào `metadata: Dict[str, Any]` thay vì nằm trực tiếp ở root level của core `State`.

### Registry Pattern

Cả Agent và Tool đều sử dụng Registry pattern:
- `AgentRegistry`: Đăng ký agent class bằng decorator `@AgentRegistry.register("name")`.
- `ToolRegistry`: Đăng ký tool bằng decorator `@ToolRegistry.register_tool(name="name")` kết hợp với `@tool()` của LangChain.

## Cost, Security & Guardrails

- **Prompt Injection Defense**: Tất cả kết quả thực thi tool từ bên ngoài (HTTP, file read, web search) được bao bọc trong các tag `<tool_output>...</tool_output>` kèm chỉ thị system prompt để LLM phân biệt dữ liệu thô và mệnh lệnh gốc.
- **Dynamic Task Limits**: Cho phép cấu hình `max_iterations` và `timeout_seconds` cho từng Task/WorkerAgent thay vì hardcode.
- **Token & Cost Budget Guardrails (target)**: Cần enforce token và wall-clock budget ở run level; không coi việc có setting là đã có guardrail. Per-task timeout/iteration controls hiện có và cần được test riêng.
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
    node: str
    status: Literal["done", "pending", "running", "failed", "skipped"]
    error: Optional[str] = None
    description: str
    dependencies: List[int] = []
    timeout_seconds: Optional[int] = None
    max_iterations: Optional[int] = None

class FlowDefinition(BaseModel):
    flow_id: str
    name: str
    tasks: List[Task]
    metadata: Dict[str, Any] = {}

class State(TypedDict):
    messages: Annotated[list[BaseMessage | str], add_messages]  # Chat history (max 10)
    plan: Annotated[list[Task], update_plan]                    # Execution plan (DAG tasks)
    current_task: Optional[Task]                                # Task đang/sắp thực thi
    logs: Annotated[list[str | LogEntry], add_logs]             # Structured or string logs
    result_storage: Annotated[list, add_results]                # Task results
    mode: Literal["conversation", "executing"]                  # Operation mode
    metadata: Dict[str, Any]                                    # Domain-specific extension data
```

Khi cập nhật State, **chỉ trả về các fields cần thay đổi** trong dict return. Reducers sẽ tự động merge.

## Testing

- Framework: `unittest` với `IsolatedAsyncioTestCase` cho async tests.
- Mock LLM: Sử dụng `unittest.mock.AsyncMock` và `MagicMock` cho `BaseChatModel`.
- Unit suite: `python scripts/run_tests.py unit` (isolated workspace, mocked HTTP/DNS, no live model).
- Legacy discovery remains available: `python -m unittest discover tests/`; it does not install the suite-level network guard.
- Real PostgreSQL/Redis suite: `python scripts/run_tests.py integration`; see `integration_tests/README.md` for dedicated targets and required safety flags. Never point it at development data.
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
