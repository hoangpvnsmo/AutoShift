# Báo cáo kiểm thử AutoShift 5.0

Ngày 25/11/2026. Windows, Python 3.14.2, OR-Tools 9.15.6755, Node 22.23.5. Số liệu là lần chạy thực tế; không tính mock/stub như lần chạy Solver.

## Kết quả

| Kiểm tra | Kết quả | Bằng chứng |
|---|---|---|
| Baseline cũ với UTF-8 | 389 PASS, 0 FAIL, 0 ERROR, 0 SKIP; 211,88 giây | artifacts/regression-baseline.log |
| Regression đầy đủ | 430 PASS, 0 FAIL, 0 ERROR, 0 SKIP; 261,36 giây tính cả nạp runner | artifacts/regression-final.log |
| Lớp 5.0 chạy lại sau thay đổi cuối | 41 PASS, không skip; 39,83 giây | artifacts/v50-final.log |
| Vue component | 3 PASS | npm test, ScheduleGrid.test.ts |
| TypeScript và Vite build | PASS | npm run build, frontend/dist |
| Development server | HTTP 200 ở 127.0.0.1:5173; đã dừng sau smoke test | start_autoshift.ps1 -Dev |
| Bản build | HTTP 200 ở 127.0.0.1:8765; tiếp tục chạy | API health và root |
| Hủy worker đang RUNNING | CANCELLED; lịch áp dụng giữ nguyên | artifacts/cancellation-final.json |
| SSE | Sự kiện cuối CANCELLED đúng trạng thái job | cùng báo cáo cancel |
| Múi giờ API | Timestamp có +07:00 | cùng báo cáo cancel |
| Sao lưu WAL | integrity_check=ok, foreign_key_check không lỗi | artifacts/export-and-backup-checks.json |
| CLI/PowerShell/dependency | compileall, parser script, pip check PASS | chạy trực tiếp trong workspace |

Lượt baseline đầu chưa dùng UTF-8 gặp UnicodeEncodeError ở console Windows; một test yêu cầu chứng minh tối ưu trong 30 giây chưa đạt trong lượt đó. Hai test chạy riêng đều PASS, rồi toàn bộ 389 test PASS với UTF-8. Không sửa/xóa test cũ hoặc core để che lỗi.

## Integration tháng 12/2026 từ workspace

Nguồn workbook và lịch Excel gốc, 21 nhân sự, 651 ô. Guide ghi Requests và Previous là minh họa. Import đối chiếu đủ 8 trường nghiệp vụ; dry-run/rollback và import lặp được kiểm tra.

- ID 2, ngày 15/12/2026: ca cũ **2**, yêu cầu mới **DT**. API trả đồng thời mã gốc và yêu cầu APPROVED cho overlay.
- Worker REPAIR thực sự dùng CP-SAT, OPTIMAL adaptive scope, ngân sách 45 giây.
- Kết quả: **2 ô thay đổi**, 2 người, 1 người khác; giữ nguyên **99,6928%** lịch, 1 ngày ảnh hưởng.
- R1–R7: `[2, 1, 24, [0, 0], 0, 1, 2]`. Không tăng công vượt/ADMIN, không phát sinh nghỉ ngắn; sai khác tổng công là 1.
- Có lần giải toàn phạm vi đạt cùng cận R1=2. Không suy diễn chứng minh toàn cục chỉ từ lần giải vùng ngày yêu cầu. Toàn bộ attempt/bound nằm trong report.json. Không chứng minh tối ưu chung R2–R8.
- Validator: 0 lỗi cứng; còn B_WORK_TIME NOT_CHECKED và 21 ô lịch sử E05 thiếu. Trạng thái **INCOMPLETE**, tách biệt với chứng minh R1.
- Lịch áp dụng không đổi khi Solver hoàn thành. Sau thao tác duyệt riêng mới chuyển PLANNED; yêu cầu mới chuyển APPLIED.
- Bản gốc `5df4cde0-81b8-44eb-bd75-d0d6b03b2327` còn nguyên. Bản mới `7d1c887c-da43-41ae-8989-ad0a3d5f91f0`. Flow HTTP/worker/export mất **37,347 giây**.
- Excel trước/sau đọc ngược đúng toàn bộ 651 mã; OFF đầy đủ, Thay doi có đúng 2 ô. Giữ sáu sheet mẫu và conditional formatting, thêm metadata/viền/sheet thay đổi.

File: artifacts/december-integration/report.json, before.xlsx, after.xlsx.

## Kế hoạch đủ 12 tháng năm nhuận 2028

Lần chạy cuối: **29,485 giây**, ngân sách 180 giây, **12 bản nháp**, **11 biên tháng không lỗi cứng**, 366 ngày và **6.588 ô Solver tự quyết định**. Sau kiểm tra, xác nhận V0 dự kiến trong transaction riêng. Không tự công bố.

Dữ liệu được khai báo rõ: 18 SHIFT, 6 người đủ quyền mỗi loại ca, tất cả là phụ trách, nhu cầu đúng 3 người/ca/ngày; không ADMIN/lễ/nguyện vọng/yêu cầu cố định trong tháng. Lịch sử đầu kỳ đủ 6 ngày từ vòng 3-on/3-off. Giới hạn đêm **3 chỉ trong fixture này**, giới hạn ngày trực 5; cấu hình người dùng vẫn là 2. E01–E06 tháng tắt trong benchmark này; Stage 3 và tầng cân bằng tích lũy năm chạy thật. Đây không phải benchmark mọi mục tiêu đang bật trên 21 nhân sự thực tế.

Có thêm test 12 tháng cố định hoàn toàn để kiểm chứng khả thi/transaction, ghi riêng trong test_v50.py; không đánh đồng với benchmark 6.588 ô tự do. Tháng hai có 29 ngày, rollback khi tháng sau vô nghiệm, duyệt thiếu một tháng không cập nhật nửa chừng, V0 không ghi đè và thiếu lịch sử không tự điền OFF đều PASS.

Báo cáo cuối: artifacts/annual-2028-20261125_034252/report.json. CSDL tái kiểm tra và Excel nằm cùng thư mục. Snapshot có phiên bản Python/thư viện và source hashes; không kế thừa proof khi môi trường khác.

## Các mốc nghiệm thu

| Mốc | Trạng thái | Điều kiện / giới hạn |
|---|---|---|
| A | Đạt audit/baseline/backup | Git cfff197 và 7e3954c; core, input, rule, workbook/checkpoint gốc còn nguyên |
| B | Đạt | 25 bảng nghiệp vụ, FK/WAL/busy timeout, migration, trigger bất biến |
| C | Đạt với workbook hiện có | Transaction/dry-run/idempotence/8 trường tương đương; dữ liệu lạ bị từ chối |
| D | Đạt các flow đã test | FRESH/RESUME DB, snapshot/runtime compatibility, worker, cancel, SSE, revision |
| E | Đạt REPAIR một tháng | Full-code R1–R8; khóa/thời gian/phạm vi; ADMIN/công vượt; đối chiếu độc lập |
| F | Đạt rolling planning và kiểm tra biên | 12 tháng CP-SAT thật; chưa backtrack hoặc chứng minh tối ưu cả năm |
| G | **Chưa có Solver REPAIR liên kết nhiều tháng** | Từ chối rõ trường hợp xuyên tháng/cần sửa tháng kế tiếp; không lưu một phần |
| H | Đạt API đã test | OpenAPI, Pydantic, xung đột, nguồn localhost, dữ liệu thật |
| I | Build/component PASS, **chưa nghiệm thu trực quan** | Computer Use dừng vì không xác minh được URL Windows |
| J | API/worker/Excel/benchmark PASS, **chưa hoàn tất browser E2E** | Giới hạn browser và Excel desktop ghi rõ |

Không gọi bản giao này là đã hoàn tất mọi yêu cầu A–J.

## Giới hạn kiểm thử

Không có browser E2E PASS: browser host không khả dụng; Computer Use Windows sau đó dừng vì không xác định được URL đủ chắc chắn để thi hành chính sách. Không dùng công nghệ browser khác để vượt qua việc dừng này. Component tests và flow HTTP là hai loại kiểm tra riêng.

Chưa render/tính lại Excel trong Excel desktop. Vite cảnh báo bundle JavaScript khoảng 1,10 MB chưa nén (361 KB gzip); build thành công. Starlette cảnh báo TestClient/httpx đã deprecated nhưng test hiện tại PASS với dependency pin. Không thay Python hệ thống hoặc cài httpx2 để che cảnh báo.
