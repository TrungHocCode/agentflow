---
description: Thêm một REST API endpoint mới trong FastAPI backend theo chuẩn AgentFlow.
---
1. Tạo router trong `backend/app/api/` (hoặc bổ sung route vào router hiện có nếu cùng resource).
2. Định nghĩa Pydantic models cho request/response schema, đặt trong module tương ứng thay vì viết trực tiếp trong route handler.
3. Dùng FastAPI dependency injection cho DB session, auth, service layer — không khởi tạo trực tiếp trong route handler.
4. Xác nhận thao tác dữ liệu đi đúng database theo bảng Data Ownership (PostgreSQL cho dữ liệu quan hệ, MongoDB cho log/kết quả thực thi, Redis cho session/cache).
5. Đăng ký router trong `backend/app/main.py`.
6. Viết test cho endpoint: request/response hợp lệ, status code, edge case lỗi.
7. Chạy test:
   // turbo
   python -m unittest discover tests/ -v
