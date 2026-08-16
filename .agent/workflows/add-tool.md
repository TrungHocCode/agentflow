---
description: Thêm một Tool mới vào ToolRegistry theo chuẩn AgentFlow (double-decorator pattern, autodiscovery, kèm unit test).
---
1. Tạo file `backend/app/execution/tools/<tool_name>_tool.py` (bắt buộc hậu tố `_tool.py` để autodiscovery hoạt động).
2. Định nghĩa Pydantic input schema kế thừa `BaseModel`, mỗi param có `Field(description=...)` rõ ràng.
3. Viết tool theo double-decorator pattern:

   ```python
   from pydantic import BaseModel, Field
   from langchain_core.tools import tool
   from app.execution.tools.base import ToolRegistry

   class MyToolInput(BaseModel):
       param: str = Field(description="Mô tả parameter.")

   @ToolRegistry.register_tool(name="my_tool")
   @tool("my_tool", args_schema=MyToolInput)
   def my_tool(param: str) -> str:
       """Mô tả tool cho LLM hiểu khi nào nên dùng."""
       return "result"
   ```

4. Xác nhận tool được autodiscover khi `app.execution.tools.registry` import — không cần đăng ký thủ công thêm.
5. Nếu tool truy cập tài nguyên bên ngoài (HTTP, file, DB), đảm bảo output được bao bọc đúng dưới dạng dữ liệu thô (không lẫn với instruction) theo nguyên tắc Prompt Injection Defense của dự án.
6. Xác nhận tool nằm trong whitelist của agent sẽ dùng nó (Tool Authorization).
7. Viết unit test tương ứng trong `tests/test_tools.py`.
8. Chạy test:
   // turbo
   python -m unittest tests.test_tools -v
