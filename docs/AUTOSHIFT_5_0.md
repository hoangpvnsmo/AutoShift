# AutoShift 5.0 — bàn giao và hướng dẫn

Ngày kiểm thử: 25/11/2026, múi giờ Asia/Bangkok. Workspace: `C:\AutoShift_4_8_clean`.

## Chạy trên Windows

Web cục bộ: **http://127.0.0.1:8765**. OpenAPI tương tác: **http://127.0.0.1:8765/docs**.

Trong PowerShell ở thư mục dự án:

```powershell
.\setup_autoshift.ps1
.\start_autoshift.ps1
```

Có thể mở `start_autoshift.bat`. Backend chạy nền ẩn, bind `127.0.0.1`. Bản build Vue được FastAPI phục vụ cùng địa chỉ; không cần hai server khi dùng bản build. Dừng bằng `stop_autoshift.ps1`. Log ở `.runtime/backend.log` và `.runtime/backend-error.log`. Không đóng hoặc can thiệp vào tiến trình khác.

Chế độ phát triển:

```powershell
.\start_autoshift.ps1 -Dev
# Vue: http://127.0.0.1:5173
# Backend: http://127.0.0.1:8765
```

Node.js portable được tải từ nodejs.org và kiểm SHA256 khi máy chưa có Node. Không sửa Python toàn hệ thống. Môi trường đã kiểm tra: Python 3.14.2, OR-Tools 9.15.6755, Node 22.23.5, SQLAlchemy 2.0.58, FastAPI 0.165.0; frontend dùng Vue 3, TypeScript, Vite, Router, Pinia, Element Plus. Dependency Python nằm trong `requirements-web.txt`, dependency frontend khóa trong `frontend/package-lock.json`. `.venv` của phiên làm việc này sử dụng OR-Tools đã cài trên máy qua system-site-packages; script thiết lập trên máy mới tạo venv độc lập rồi cài dependency.

## Dữ liệu và import

SQLite chính: `data/autoshift.db`. Khi CSDL đã có dữ liệu, khởi động không import lại hoặc ghi đè lịch.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m autoshift5 init
.\.venv\Scripts\python.exe -X utf8 -m autoshift5 import --schedule output/AutoShift_2026-12_4_9_20261125_021312_782403.xlsx
# Mặc định dry-run, toàn bộ thao tác được rollback.
.\.venv\Scripts\python.exe -X utf8 -m autoshift5 import --schedule output/AutoShift_2026-12_4_9_20261125_021312_782403.xlsx --commit
```

Import đọc Employees, Month, Holidays, Requests, Previous, Preferences và lưu toàn bộ hàng Guide/ghi chú trong báo cáo nguồn. Sheet/cột chưa nhận diện, công thức trong bảng nghiệp vụ, ID/ngày/mã sai, trùng yêu cầu và lịch thiếu mã bị từ chối. Import lặp cùng nguồn trả `already_imported`; nguồn khác cho tháng đã tồn tại bị từ chối, không tự ghi đè. Lịch gốc được đọc từ sheet **Schedule đầy đủ mã**. Không dựng lại lịch gốc từ checkpoint 0/1/2/3 bằng Requests mới, không nhập chứng minh tối ưu từ Excel/checkpoint cũ.

Đã kiểm tra tương đương toàn bộ 8 trường MonthData/phần nguyện vọng: year, month, holidays, employees, requests, previous, rules, preferences. Thứ tự Requests/Preferences được chuẩn hóa khi so sánh; nội dung, ghi chú, quyền, công chuẩn và toàn bộ cấu hình giữ nguyên.

Workbook đang có 21 nhân sự, 651 ô trong tháng 12/2026; đây là dữ liệu của workspace, không phải hằng số của ứng dụng. **Guide của workbook ghi Requests và Previous là minh họa**, vì vậy không coi chúng là dữ liệu vận hành đã xác nhận. Previous chỉ có 5 ngày: đủ hard rules cũ hiện tại nhưng thiếu 21 ô ngày thứ sáu cho E05. Không điền OFF để che phần thiếu. Giới hạn ca đêm gốc vẫn là **2**.

## Thao tác web

1. Chọn tháng, phiên bản và bộ lọc nhóm/nhân sự. OFF hiển thị trống; dấu `·` nghĩa là chưa xếp lịch. Ca và mã công giữ màu mẫu Excel 4.9. Tổng công/ca ở phải, số người và phụ trách từng ca được kiểm tra ở cuối bảng.
2. Tạo yêu cầu từ ô lịch hoặc màn hình Yêu cầu. Có thể chọn nhiều người, nhóm, khoảng ngày và khoảng xuyên tháng đã có kế hoạch. Yêu cầu APPROVED hiển thị thêm trên ca cũ, không thay mã lịch gốc. Tắt/bật overlay trong thanh lọc.
3. DRAFT → SUBMITTED → APPROVED → APPLIED được phân biệt. APPLIED chỉ được gán khi xác nhận phiên bản thực sự chứa mã yêu cầu. Hủy/thay thế lưu revision; xung đột không bị ghi đè ngầm. Nguyện vọng mềm có tab và trọng số riêng.
4. Nhấp ô để xem yêu cầu/khóa; thay đổi ô chỉ trên DRAFT. Hoàn tác/làm lại trước khi lưu. Lưu tạo phiên bản con, kiểm tra cả tháng, khóa, ca đã bắt đầu và biên; không sửa bản nháp cũ tại chỗ.
5. Kết quả Solver luôn là DRAFT. Chọn xem toàn bộ, chỉ vùng ảnh hưởng hoặc mã cũ/mới trong cùng ô. Xem vector, công trước/sau, ADMIN và nghỉ ngắn trong phần kết quả/log. Duyệt PLANNED là thao tác riêng; công bố/chốt ACTUAL yêu cầu kiểm tra đầy đủ và không đổi lịch đã thực hiện.
6. Lịch sử cho phép xem phiên bản trước và khôi phục thành bản nháp con, không xóa lịch sử. Để bỏ một phương án, trở về lịch đang áp dụng; bản nháp vẫn được giữ để truy vết.

### FRESH và RESUME

FRESH khởi tạo đường checkpoint riêng của job, độc lập checkpoint gốc. RESUME chọn phiên bản đầy đủ mã làm nghiệm nền. Chứng minh chỉ được tái dùng khi fingerprint core/input/objective tương thích, source ứng dụng còn khớp và nghiệm được kiểm tra lại. Chỉ khóa điểm đã chứng minh, không khóa toàn ma trận.

Chọn Tối ưu tiếp và số lượt (mặc định tối đa 3), tổng thời gian. Dừng khi chứng minh xong hoặc hai lượt liên tiếp không cải thiện; luôn so theo vector mục tiêu tháng đang bật. Khi lịch nền có yêu cầu mới chưa áp dụng, RESUME yêu cầu dùng REPAIR. Nếu chủ động muốn xếp lại toàn bộ tháng, chọn **Lập lịch mới (FRESH)**; chế độ này được phép dùng tất cả yêu cầu đã duyệt mới nhất dù tháng đã có lịch.

Để lập riêng T12/2026 từ đầu: chọn tháng 12/2026, vào Yêu cầu & nguyện vọng để rà soát/hủy yêu cầu cũ không còn dùng, thêm BKD/CT với trạng thái Đã duyệt, rồi về Lịch trực → Lập lịch mới. Không cần chạy kế hoạch năm. Kết quả mới là DRAFT để xem/kiểm tra/duyệt; bản cũ vẫn ở lịch sử. CT tính 1 công, BKD 0 công theo cấu hình hiện tại; cả hai không được bố trí ca trực trong ngày đó.

### REPAIR

Chọn bản tham chiếu, yêu cầu đã duyệt/đã hủy cần áp dụng, FAST hoặc OPTIMAL, `as_of` có múi giờ, vùng ngày tối đa, số ô đổi tối đa và hạn chế ADMIN/công vượt.

Mỗi ô có biến mã đầy đủ và biến thay đổi tương đương hai chiều. Một lần ca 1 → ca 2, OFF → DT hoặc T → ca trực đều là một ô. Tối ưu tuần tự R1..R8, từng thành phần trong vector; không dùng trọng số khổng lồ. Mỗi tầng sau khóa điểm đạt ở tầng trước. R8 dùng lại các biểu thức Stage 3, E01–E06 và E05 Flexible Rotation hiện hành, không khóa điểm chất lượng của baseline.

R4 gồm tăng tổng công vượt và tăng ca ADMIN so với baseline; R5 đếm cặp nghỉ ngắn mới; R6 tổng sai khác công từng người; R7 sai khác V0. R3 dùng ưu tiên lịch gần ngày thực hiện và lịch công bố. Toàn bộ vector được tính độc lập từ mã lịch và đối chiếu với biểu thức Solver trước khi chấp nhận.

FAST tìm vùng ngày yêu cầu rồi ±1, ±3, ±7 và toàn phạm vi. OPTIMAL tiếp tục mở rộng trong ngân sách và so vector các nghiệm đã kiểm tra. Có thể giữ nghiệm ở vùng nhỏ nhưng nhận chứng minh R1 từ một lần giải toàn phạm vi có cùng cận; metadata mới lưu riêng phạm vi nghiệm và chứng nhận cận R1. FEASIBLE/vùng hẹp không tự trở thành chứng minh toàn cục. UNKNOWN và INFEASIBLE được phân biệt.

Ca 1 ngày D bắt đầu lúc 22:00 ngày D−1 theo cấu hình gốc. Không đổi hoặc thêm ca đã bắt đầu. Khi giờ phi ca chưa đủ, khóa bảo thủ từ đầu ngày; BKD không làm việc. Không áp ngưỡng nghỉ 8 giờ cho CT/DT/T. Khi duyệt, thời điểm hiện tại và các ô khóa được kiểm tra lại để chống trường hợp một ca bắt đầu trong lúc Solver chạy.

### ANNUAL

Tạo kế hoạch từ tháng nguồn, khai báo giả định, chỉnh từng tháng rồi chạy Lập lịch năm. Tháng đầu cần lịch sử thực tế liên tiếp đủ chiều dài theo cấu hình (ít nhất 6 ngày cho E05). Các tháng tiếp theo nhận mã đầy đủ của tháng đã giải trước đó. Không giả định OFF hoặc ngày lễ/yêu cầu của tháng khác.

Solver tháng giữ công chuẩn, quyền và nhu cầu của từng tháng. Sau toàn bộ mục tiêu tháng đang bật, tầng cân bằng tích lũy so các nhóm cùng vai trò, quyền ca và công chuẩn theo ca 1/2/3, lễ, cuối tuần không trùng lễ, cuối tuần nghỉ trọn, công vượt, nghỉ ngắn. Đây là tối ưu cuốn chiếu, không phải chứng minh tối ưu chung cả năm.

Toàn bộ tháng và biên được kiểm tra trước khi lưu các bản nháp trong cùng transaction. Nếu một tháng thất bại, không lưu lịch một phần. Xác nhận toàn kế hoạch đóng băng V0 trong một transaction riêng; không tự công bố. Lịch cá nhân, thống kê, số ô thay đổi, số lần thay người và sai khác V0 nằm trong màn hình năm.

## Validator và giờ phi ca

Trạng thái Validator tách biệt với chứng minh tối ưu. `INCOMPLETE` / hard pending không được hiển thị là hợp lệ hoàn toàn. Lịch minh họa hiện tại còn `B_WORK_TIME` và lịch sử E05 thiếu; có thể duyệt lịch dự kiến với nhãn này, **chưa đủ điều kiện PUBLISHED/ACTUAL**.

Màn hình Cấu hình có form khai báo giờ CT/DT/T và ngưỡng nghỉ phi ca **do người quản lý xác nhận**; BKD luôn nghỉ. Chức năng được ghi rõ **chỉ phục vụ Validator, chưa đưa vào mô hình Solver**. Không có giờ mặc định hoặc ngưỡng 8 giờ mặc định. Thay đổi tạo rule profile mới, snapshot cũ không biến đổi. Solver có thể bị từ chối bởi Validator nếu chính sách giờ phi ca phát hiện vi phạm; chưa có cơ chế tự tìm phương án để khắc phục các rule giờ này.

Các tham số JSON cũ chưa có hiệu lực độc lập (`prefer_staffing`, các công tắc ADMIN riêng...) vẫn được giữ để tương thích, không đưa lên giao diện như công tắc đang hoạt động.

## Excel

Xuất tháng từ phiên bản đang xem: giữ sáu sheet Lich truc, Thong ke, Bo tri ca, Kiem tra, Schedule, Huong dan. Sheet Schedule luôn lưu OFF rõ ràng; Lich truc dùng công thức hiển thị OFF trống. REPAIR thêm Thay doi, viền ô đổi giữ nguyên màu loại ca. Huong dan ghi version, parent, snapshot/digest, Validator, trạng thái và chứng minh. File có tên duy nhất, mở theo chế độ tạo mới, không ghi đè.

Xuất năm/lịch cá nhân có mã đầy đủ, version và metadata snapshot/Validator từng tháng. Công thức mẫu 4.9 giữ nguyên; Excel tính lại khi mở. Đã kiểm tra đọc ngược sheet Schedule; chưa kiểm chứng trực tiếp render/tính lại bằng Excel desktop trong phiên này.

## Sao lưu và khôi phục

Không copy riêng file `.db` đang chạy vì có WAL. Lệnh sau dùng SQLite online backup, bao gồm dữ liệu trong WAL:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m autoshift5 backup C:\AutoShift_4_8_clean\artifacts\backup_20261125.db
```

Tên đích phải chưa tồn tại. Khôi phục an toàn bằng một đường dẫn mới, giữ CSDL đang dùng để đối chứng:

```powershell
.\stop_autoshift.ps1
# Chọn bản backup đã kiểm tra, không xóa/ghi đè CSDL cũ.
$env:AUTOSHIFT_DB = 'C:\AutoShift_4_8_clean\artifacts\backup_20261125.db'
.\.venv\Scripts\python.exe -X utf8 -m uvicorn autoshift5.api:app --host 127.0.0.1 --port 8765
```

API startup bật foreign keys, WAL, busy timeout 15 giây, khôi phục trạng thái job bị gián đoạn thành INTERRUPTED. Migration dùng Alembic revision `0001`; downgrade phá hủy bị chặn, khôi phục bằng backup thay vì xóa lịch sử.

## Kết quả kiểm thử và giới hạn

Chi tiết đo thực tế ở [TEST_REPORT_5_0.md](TEST_REPORT_5_0.md), kiến trúc/schema ở [ARCHITECTURE_5_0.md](ARCHITECTURE_5_0.md), danh sách file ở [FILES_5_0.md](FILES_5_0.md). OpenAPI JSON được lưu ở `docs/openapi-5.0.json`.

Các giới hạn còn lại cần phân biệt rõ với tính năng đã hoàn tất:

- **Chưa có Solver REPAIR liên kết nhiều tháng.** API từ chối yêu cầu REPAIR xuyên tháng và phương án gây lỗi cho tháng kế tiếp; không tự sửa một phần, không nới rule. Người dùng có thể nhập yêu cầu xuyên tháng để quản lý, nhưng chưa thể chạy một REPAIR chung cho chúng. Đây là giới hạn chức năng quan trọng của mốc G.
- Rolling Horizon chưa backtrack tháng trước khi một tháng sau không giải được; job thất bại an toàn và không lưu một phần. Chưa chứng minh tối ưu toàn năm.
- Quyền/vai trò thay đổi theo tháng. Core chưa hỗ trợ đổi quyền giữa tháng; trường hợp đó bị từ chối rõ ràng. Nhân sự đã có lịch không bị xóa vật lý.
- Chỉ một job Solver hoạt động trên workspace tại một thời điểm; khóa revision toàn CSDL hơi bảo thủ, có thể đánh STALE khi sửa một tháng khác. Hủy tiến trình riêng an toàn; chỉ giữ các phiên bản đã kiểm tra và commit, không cố giữ nghiệm giữa một lần CP-SAT chưa hoàn thành kiểm tra.
- Log và trạng thái có thật, không tạo phần trăm tiến độ giả. FRESH/RESUME chưa có callback tiến trình cho từng nghiệm bên trong tầng đang giải; chi tiết điểm/lượt/ALNS được ghi sau khi tầng trả kết quả.
- Giao diện đã build, type-check và kiểm tra component. **Chưa xác nhận trực quan/E2E bằng trình duyệt**: browser host không khả dụng; Computer Use Windows sau đó dừng vì không xác định được URL đủ chắc chắn để thi hành chính sách. Không đánh đồng component/API test với kiểm thử trình duyệt.
- Chưa kiểm tra render Excel desktop. Không coi kết quả INCOMPLETE hiện tại là lịch chính thức.

Do các giới hạn trên, đây là bản 5.0 có thể chạy và kiểm thử cục bộ, **chưa nghiệm thu trọn bộ mọi yêu cầu A–J**. Không có chức năng giả để che các phần còn thiếu.
