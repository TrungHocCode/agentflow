---
description: Thêm một Agent mới (kế thừa BaseAgent/WorkerAgent/SupervisorAgent) và đăng ký vào AgentRegistry.
---
1. Xác định agent là Worker hay Supervisor; tạo class kế thừa `BaseAgent` (hoặc `WorkerAgent`/`SupervisorAgent`) tương ứng.
2. Implement `async def execute(self, state: State) -> Dict[str, Any]` — chỉ trả về các field cần thay đổi, để reducer tự merge vào `State`.
3. Đăng ký agent bằng decorator:

   ```python
   from app.execution.agents.base import WorkerAgent
   from app.execution.agents.registry import AgentRegistry

   @AgentRegistry.register("my_agent")
   class MyAgent(WorkerAgent):
       async def execute(self, state):
           pass
   ```

4. Nếu là WorkerAgent: xác nhận whitelist tools, đặt `max_iterations`/`timeout_seconds` phù hợp với task để tránh vòng lặp vô hạn.
5. Nếu agent tham gia Build Phase (lập kế hoạch), đảm bảo output là `FlowDefinition`/`Task` hợp lệ theo State Schema, không tự thực thi task.
6. Viết test trong `tests/test_agents_base.py`, dùng `AsyncMock`/`MagicMock` để mock `BaseChatModel`.
7. Chạy test:
   // turbo
   python -m unittest tests.test_agents_base -v
