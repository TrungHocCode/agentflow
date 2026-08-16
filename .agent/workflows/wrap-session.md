---
description: Cập nhật docs/STATUS.md dựa trên các thay đổi đã thực hiện trong phiên làm việc hiện tại, để phiên/agent tiếp theo nắm được tiến độ mà không cần hỏi lại.
---
1. Xem lại các thay đổi đã tạo ra trong session này:
   // turbo
   git status
   // turbo
   git diff dev --stat
2. Đọc file `docs/STATUS.md` hiện tại để biết trạng thái trước đó (nếu chưa có file, tạo mới theo cấu trúc chuẩn: Đã hoàn thành / Đang làm dở / Vấn đề đã biết / Bước tiếp theo / Quyết định gần đây).
3. Đối chiếu git diff với nội dung đã trao đổi trong conversation này để xác định:
   - Mục nào trong "Đang làm dở" nay đã hoàn thành → chuyển sang "Đã hoàn thành", nhóm theo đúng module (Build Phase, Run Phase, Agents, Tools, API, Frontend).
   - Việc mới bắt đầu nhưng chưa xong → thêm vào "Đang làm dở", ghi rõ còn thiếu phần nào.
   - Lỗi/giới hạn phát hiện trong lúc làm nhưng chưa xử lý → thêm vào "Vấn đề đã biết".
   - Quyết định kỹ thuật đáng chú ý đã đưa ra trong session (ví dụ chọn cách tiếp cận, đổi thiết kế) → thêm vào "Quyết định gần đây".
4. Cập nhật "Bước tiếp theo" dựa trên phần còn dang dở gần nhất — viết ngắn gọn, đủ để phiên sau đọc là hiểu ngay việc cần làm tiếp, không cần hỏi lại.
5. Cập nhật "Cập nhật lần cuối" (ngày hôm nay) và "Nhánh hiện tại" ở đầu file.
6. Không xoá các mục lịch sử đã có trong "Đã hoàn thành" hay "Quyết định gần đây" — chỉ thêm, trừ khi người dùng yêu cầu dọn dẹp.
7. Hiển thị lại phần vừa thay đổi trong STATUS.md để người dùng xác nhận trước khi coi là xong; không tự ý commit thay đổi này.
