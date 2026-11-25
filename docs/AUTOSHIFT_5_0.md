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

Khi tắt sửa liên kết, FRESH khởi tạo đường checkpoint riêng của job, độc lập checkpoint gốc. RESUME chọn phiên bản đầy đủ mã làm nghiệm nền. Chứng minh chỉ được tái dùng khi fingerprint core/input/objective tương thích, source ứng dụng còn khớp và nghiệm được kiểm tra lại. Chỉ khóa điểm đã chứng minh, không khóa toàn ma trận. FRESH bật sửa liên kết dùng mô hình CP-SAT ghép tháng mô tả bên dưới, không dùng checkpoint hoặc nghiệm cũ của tháng gốc làm hint.

Chọn Tối ưu tiếp và số lượt (mặc định tối đa 3), tổng thời gian. Dừng khi chứng minh xong hoặc hai lượt liên tiếp không cải thiện; luôn so theo vector mục tiêu tháng đang bật. Khi lịch nền có yêu cầu mới chưa áp dụng, RESUME yêu cầu dùng REPAIR. Nếu chủ động muốn xếp lại toàn bộ tháng, chọn **Lập lịch mới (FRESH)**; chế độ này được phép dùng tất cả yêu cầu đã duyệt mới nhất dù tháng đã có lịch.

Để lập riêng T12/2026 từ đầu: chọn tháng 12/2026, vào Yêu cầu & nguyện vọng để rà soát/hủy yêu cầu cũ không còn dùng, thêm BKD/CT với trạng thái Đã duyệt, rồi về Lịch trực → Lập lịch mới. Không cần chạy kế hoạch năm. Kết quả mới là DRAFT để xem/kiểm tra/duyệt; bản cũ vẫn ở lịch sử. CT tính 1 công, BKD 0 công theo cấu hình hiện tại; cả hai không được bố trí ca trực trong ngày đó.

### REPAIR

Chọn bản tham chiếu, yêu cầu đã duyệt/đã hủy cần áp dụng, FAST hoặc OPTIMAL, `as_of` có múi giờ, vùng ngày tối đa, số ô đổi tối đa và hạn chế ADMIN/công vượt.

Mỗi ô có biến mã đầy đủ và biến thay đổi tương đương hai chiều. Một lần ca 1 → ca 2, OFF → DT hoặc T → ca trực đều là một ô. Tối ưu tuần tự R1..R8, từng thành phần trong vector; không dùng trọng số khổng lồ. Mỗi tầng sau khóa điểm đạt ở tầng trước. R8 dùng lại các biểu thức Stage 3, E01–E06 và E05 Flexible Rotation hiện hành, không khóa điểm chất lượng của baseline.

R4 gồm tăng tổng công vượt và tăng ca ADMIN so với baseline; R5 đếm cặp nghỉ ngắn mới; R6 tổng sai khác công từng người; R7 sai khác V0. R3 dùng ưu tiên lịch gần ngày thực hiện và lịch công bố. Toàn bộ vector được tính độc lập từ mã lịch và đối chiếu với biểu thức Solver trước khi chấp nhận.

FAST tìm vùng ngày yêu cầu rồi ±1, ±3, ±7 và toàn phạm vi. OPTIMAL tiếp tục mở rộng trong ngân sách và so vector các nghiệm đã kiểm tra. Có thể giữ nghiệm ở vùng nhỏ nhưng nhận chứng minh R1 từ một lần giải toàn phạm vi có cùng cận; metadata mới lưu riêng phạm vi nghiệm và chứng nhận cận R1. FEASIBLE/vùng hẹp không tự trở thành chứng minh toàn cục. UNKNOWN và INFEASIBLE được phân biệt.

Ca 1 ngày D bắt đầu lúc 22:00 ngày D−1 theo cấu hình gốc. Không đổi hoặc thêm ca đã bắt đầu. Khi giờ phi ca chưa đủ, khóa bảo thủ từ đầu ngày; BKD không làm việc. Không áp ngưỡng nghỉ 8 giờ cho CT/DT/T. Khi duyệt, thời điểm hiện tại và các ô khóa được kiểm tra lại để chống trường hợp một ca bắt đầu trong lúc Solver chạy.

#### Repair có cân bằng trong giới hạn ô đổi

Mặc định bật **Cân bằng ca khi Repair**, được thêm tối đa **2 ô cho toàn gói** so với phương án Repair gốc. Có thể chọn 0, 1 hoặc 2; giới hạn cứng số ô đổi do người quản lý đặt vẫn được ưu tiên. Tắt tùy chọn để dùng nguyên luồng R1–R8 cũ. API dùng `repair_balance: true` và `repair_balance_extra: 2` trong `POST /api/jobs`.

Khi bật, dành khoảng 65% ngân sách cho Repair gốc và tối đa 35% để hiệu chỉnh. Bước sau dùng nghiệm gốc làm dự phòng, giữ nguyên phạm vi tháng đã giải, biên phải, ô khóa, phần đã thực hiện, requests và các giới hạn ADMIN/công vượt. Tổng số nghỉ ngắn mới và điểm tăng nguồn lực R4 không được lớn hơn phương án Repair gốc. Phần cân có thể dùng các ngày khác trong khoảng người quản lý đã cho phép, kể cả khi FAST tìm được nghiệm gốc ở một vùng ngày hẹp.

Trong dải số ô được phép, ưu tiên độ lệch tổng ca các tháng đã có lịch trong năm, tổng ca tháng đang sửa, ca đêm năm và ca đêm tháng; mỗi nhóm xét lệch lớn nhất rồi tổng độ lệch. Trọng số theo ngày còn lại sau OFF/CT/DT/BKD và tỷ lệ công chuẩn; mục tiêu ca đêm chỉ phân cho người có quyền đêm. Các tháng ngoài vùng sửa đóng góp thống kê cố định và không được sửa. Tháng chưa có lịch không được điền OFF. Sau công bằng mới xét số ô, số tháng và các tầng ổn định còn lại. E01–E06 được tính lại độc lập, không cam kết giữ nguyên các điểm cũ ngoài các giới hạn đã nêu.

Chỉ nhận nghiệm đã được kiểm tra và đối chiếu điểm. Nếu không cải thiện công bằng thì không tiêu thêm ô; hết thời gian hoặc chưa hỗ trợ điều kiện cân thì giữ phương án Repair gốc và báo rõ. Bảng kết quả so lịch cũ, Repair gốc và sau cân theo tổng ca/ca đêm, kèm số ô và nghỉ ngắn. Khi tiêu thêm ô, kết quả không gắn chứng minh R1 nhỏ nhất; chứng nhận của Repair gốc được giữ riêng. Nếu số ô vẫn bằng mốc đã chứng minh tối thiểu, chứng minh R1 có thể được giữ, không suy ra tối ưu toàn bộ chất lượng hoặc công bằng.

### Sửa liên kết các tháng sau

Trong FRESH hoặc REPAIR, bật **Cho phép sửa liên kết các tháng sau** (giao diện bật sẵn), đặt số tháng sau được phép sửa, tối đa 24. Chỉ lấy chuỗi tháng liên tiếp đã có lịch áp dụng; không tự tạo lịch cho tháng còn trống. API dùng `cascade: true, cascade_months: 12`; mặc định API là false để tương thích ứng dụng cũ.

Ví dụ xếp lại T12/2026: nhập/hủy BKD, CT và các yêu cầu cần thiết, chọn FRESH, bật sửa liên kết. Solver thử T12 mới với T1 giữ nguyên trước. Nếu không có nghiệm, mở vùng T12–T1 và giữ T2 cố định; tiếp tục thêm T2 khi cần, luôn kiểm tra với tháng ngay sau vùng sửa. Giữ lịch tháng trước, ô khóa, ACTUAL và ca đã bắt đầu. Không đổi âm thầm lịch đã công bố.

FRESH ưu tiên giảm tổng số ô đổi ở các tháng **sau** tháng gốc; tháng gốc được xếp lại tự do theo yêu cầu mới. REPAIR tính cả tháng gốc. Sau tổng số ô đổi là số tháng thay đổi, các tầng ổn định R2–R7 và chất lượng R8 theo thứ tự. Mỗi thành phần được khóa trước khi tối ưu thành phần kế tiếp. Phạm vi ngày chỉ giới hạn tháng gốc; `max_changes` giới hạn tổng số ô thuộc mục tiêu ổn định. Những yêu cầu tương lai được chọn rõ ràng hoặc tháng có đầu vào nghiệp vụ mới trong horizon buộc vùng giải bao gồm tháng đó; không dùng snapshot cũ để bỏ qua yêu cầu đã duyệt mới. Nguyện vọng mềm mới không tự buộc mở rộng vùng.

FAST dừng ở vùng nhỏ đầu tiên có nghiệm đã kiểm tra. OPTIMAL tiếp tục mở rộng trong ngân sách để có thể giảm tổng số ô đổi; không coi tên chế độ là bằng chứng tối ưu. Chỉ gắn chứng minh R1 khi đạt cận dưới 0 hoặc CP-SAT xác nhận tối ưu trên toàn vùng tháng đã cho phép, với tháng ngoài vùng giữ cố định. Metadata ghi rõ scope và chứng nhận; không chứng minh tối ưu chất lượng hoặc toàn năm.

Kết quả hiển thị từng tháng, số ô đổi so với lịch áp dụng, số người và các biên đã kiểm tra. Chọn **Xem cũ / mới** để đối chiếu, rồi **Duyệt toàn bộ gói dự kiến**. API `POST /api/jobs/{job_id}/confirm` nhận revision, expected_current_version_id của tháng gốc và status. Tất cả phiên bản được lưu nháp trong cùng transaction và được duyệt trong cùng transaction khác. Chặn duyệt hoặc sửa riêng từng thành viên nháp; đổi yêu cầu rồi chạy lại gói để tìm phương án khác. Nếu revision, lịch áp dụng, khóa hoặc thời điểm bắt đầu ca thay đổi, toàn bộ lần duyệt bị từ chối. V0 năm và lịch sử cũ không bị ghi đè.

### ANNUAL

Tạo kế hoạch từ tháng nguồn, khai báo giả định, chỉnh từng tháng rồi chạy Lập lịch năm. Tháng đầu cần lịch sử thực tế liên tiếp đủ chiều dài theo cấu hình (ít nhất 6 ngày cho E05). Các tháng tiếp theo nhận mã đầy đủ của tháng đã giải trước đó. Không giả định OFF hoặc ngày lễ/yêu cầu của tháng khác.

Solver tháng giữ công chuẩn, quyền và nhu cầu của từng tháng. Mặc định dành 60% ngân sách để dựng lịch các tháng và 40% để cân cả kế hoạch trước khi lưu V0. Tầng cân mới ưu tiên nguồn lực, tổng ca trực 1/2/3 trong năm, ca T7/CN kể cả trùng lễ, cơ hội nghỉ trọn cuối tuần theo giờ thực, rồi độ chênh công tháng và số ô đổi. Không khóa các điểm chất lượng tháng đã đạt ở lượt dựng ban đầu để cản việc cân năm. Các điểm E01–E06 được tính lại độc lập; không được xem là giữ nguyên hoặc đã tối ưu sau khi cân năm. Nếu không còn ngân sách để cân thì kết quả ghi rõ NOT_RUN_BUDGET_EXHAUSTED. Đây là tìm kiếm có giới hạn thời gian, không phải chứng minh tối ưu chung cả năm.

Toàn bộ tháng và biên được kiểm tra trước khi lưu các bản nháp trong cùng transaction. Nếu một tháng thất bại, không lưu lịch một phần. Xác nhận toàn kế hoạch đóng băng V0 trong một transaction riêng; không tự công bố. Lịch cá nhân, thống kê, số ô thay đổi, số lần thay người và sai khác V0 nằm trong màn hình năm.

### Clear năm để kiểm thử hoặc lập lại từ đầu

Vào **Kế hoạch năm**, chọn đúng năm, bấm **Clear năm**, xem phạm vi và xác nhận **Clear năm 2027** (hoặc năm đã chọn). Sau khi hoàn tất, bấm **Lập lịch năm**; không cần tạo lại 12 tháng.

Clear đưa mọi tháng hiện có của năm đã chọn về chưa xếp, bỏ liên kết lịch áp dụng/V0 và khóa ô của lịch cũ, mở lại bước lập V0. Giữ nguyên nhân sự, quyền ca, công chuẩn, quy tắc, chính sách giờ, ngày lễ, định biên, yêu cầu CT/DT/BKD/OFF và nguyện vọng. Yêu cầu APPLIED trở về APPROVED để Solver áp dụng lại; các trạng thái khác giữ nguyên. Lịch các năm khác không thay đổi. Lịch sử dẫn xuất của các tháng sẽ được dựng lại, giữ mốc lịch sử trước tháng đầu.

Hệ thống tự tạo SQLite online backup trong `artifacts/backups/before_clear_<năm>_*.db` trước khi thay đổi. Bản lịch, ô phân công và snapshot cũ vẫn nguyên vẹn trong lịch sử; các kết quả Solver cũ liên quan chuyển ARCHIVED và không thể duyệt lại. Clear không phải xóa vật lý dữ liệu. Nhật ký CLEAR_YEAR ghi đường dẫn backup, phiên bản cũ và phạm vi thay đổi.

Chặn Clear khi đang có Solver chạy, lịch áp dụng đã PUBLISHED/ACTUAL hoặc đã có ca/ngày bắt đầu. Hộp xác nhận dùng revision riêng; dữ liệu đổi sau khi xem trước thì cần mở lại hộp. Lập lại ANNUAL vẫn kiểm tra biên với tháng kế tiếp đang áp dụng, kể cả khi tắt hoặc hết thời gian cân năm; xung đột thì không lưu một phần.

API: `GET /api/annual-plans/{id}/clear-preview`, sau đó `POST /api/annual-plans/{id}/clear` với `year` và `revision` đã xem trước. Bấm lại khi đã trống không tạo backup hoặc tăng revision lần nữa.

### Cân bằng năm định kỳ — BALANCE

Sau khi đã có V0, vào **Kế hoạch năm**, chọn năm và bấm **Cân bằng năm**. Đặt tổng ngân sách, tháng bắt đầu được phép sửa, số tháng mỗi vùng CP (mặc định 3) và giới hạn số ô đổi nếu cần. Phần đã thực hiện theo thời điểm as_of, ACTUAL, PUBLISHED, ô khóa và tháng trước phạm vi đều được giữ cố định. Toàn bộ phần đã qua vẫn được tính vào tổng năm để phần tương lai bù dần. Cần REPAIR áp dụng các yêu cầu mới trước khi chạy BALANCE.

Mục tiêu chính là số ca trực, không phải công quy đổi có CT/DT/T. Mỗi nhân sự SHIFT có trọng số theo số ngày khả dụng sau yêu cầu OFF/CT/DT/BKD và tỷ lệ công chuẩn riêng so với công chuẩn lịch tháng. Không tách nhóm phụ trách khỏi nhóm trực khi cân tổng ca; quyền và phụ trách vẫn là rule cứng. Ca T7/CN có trọng số khả dụng riêng. Mục tiêu từng người hiển thị khoảng nguyên dưới/trên theo tỷ trọng và tổng ca thực tế cần phân bổ, không ép một người vắng dài ngày bằng người đủ cả năm.

Luồng tìm kiếm dùng lịch hiện có làm nghiệm nền, thử chuyển ca đơn, hoán đổi ca cuối tuần kèm ngày thường và hoán đổi cụm tuần. Chỉ nhận thay đổi cải thiện vector sau khi kiểm tra rule gốc, không giảm tổng số cuối tuần nghỉ trọn của cả đội so với lịch ban đầu. Khi tổng ca hoặc ca cuối tuần đã đạt mức chia đều số học ở nhóm đồng điều kiện, chuyển sớm sang mục tiêu tiếp theo. Phần thời gian còn lại mở các vùng CP-SAT ghép tháng nhỏ, giữ các tháng khác cố định. Mô hình vùng bỏ việc dựng toàn bộ biến chất lượng tháng không tham gia mục tiêu, đồng thời khóa các tầng đã đạt cận số học để tập trung phần chưa đạt. Khi Solver hòa điểm ở tầng hiện tại nhưng làm xấu tầng sau, giữ nghiệm tốt hơn đã có. Mọi điểm sau cùng đều được tính lại độc lập; FEASIBLE không được gắn nhãn tối ưu toàn năm hoặc ít ô đổi nhất.

Màn hình năm hiển thị tổng ca, công quy đổi, số ca đã bắt đầu theo lịch và số ca còn tới khoảng mục tiêu. Thống kê nghỉ trọn ghép đủ thứ Bảy, Chủ nhật và ca đêm tối Chủ nhật của tháng kế tiếp; thiếu dữ liệu báo chờ, không mặc định nghỉ. Kết quả BALANCE có bảng trước/sau và từng nhân sự, lưu cả gói DRAFT. Chọn **Duyệt toàn bộ gói dự kiến** để áp dụng; V0 và phiên bản cũ giữ nguyên. Có thể cân lại hàng tháng sau khi cập nhật ca thực tế/yêu cầu, chỉ mở phần tương lai chưa chốt.

API: `POST /api/jobs` với `mode: "BALANCE"`, `annual_plan_id`, `revision`, `budget`, `balance_from_month`, `balance_window_months`, `as_of` và `max_changes` nếu cần. Duyệt qua `POST /api/jobs/{job_id}/confirm`. `annual_balance: false` chỉ là tùy chọn tương thích để giữ luồng ANNUAL cũ; giao diện mặc định bật phương pháp mới.

## Validator và giờ phi ca

Trạng thái Validator tách biệt với chứng minh tối ưu. `INCOMPLETE` / hard pending không được hiển thị là hợp lệ hoàn toàn. Lịch minh họa hiện tại còn `B_WORK_TIME` và lịch sử E05 thiếu; có thể duyệt lịch dự kiến với nhãn này, **chưa đủ điều kiện PUBLISHED/ACTUAL**.

Job `BOUNDARY_CONFLICT` nghĩa là đã tìm được lịch cho tháng đang giải, nhưng nó không khớp lịch tháng liền kề đã xác nhận (ví dụ T12 thay đổi làm T1 bị quá chuỗi đêm hoặc thiếu giờ nghỉ). Phương án này không được lưu/áp dụng. `STALE` dành cho đầu vào hoặc phiên bản áp dụng đổi trong khi giải. Những job cũ gán nhầm STALE cho lỗi biên được hiển thị lại đúng loại, không sửa lịch hay bản ghi lịch sử gốc. Dòng `preferences FEASIBLE (2,) -> (1,)` là thông báo giảm điểm phạt nguyện vọng, không phải lỗi cứng.

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

- Sửa liên kết hiện chỉ mở về các tháng sau và yêu cầu chuỗi lịch áp dụng liên tiếp. Không tự sửa tháng trước hoặc đi qua tháng trống. Chưa hỗ trợ đổi giờ bắt đầu/kết thúc loại ca giữa các tháng trong cùng gói; thay đổi quyền/vai trò và ngưỡng nghỉ vẫn được đọc theo tháng. Giờ phi ca vẫn chỉ được kiểm tra bởi Validator, chưa được đưa vào Solver. Hết ngân sách có thể trả UNKNOWN hoặc bản nháp chưa chứng minh tối ưu.
- Rolling Horizon chưa backtrack tháng trước khi một tháng sau không giải được; job thất bại an toàn và không lưu một phần. Chưa chứng minh tối ưu toàn năm.
- Quyền/vai trò thay đổi theo tháng. Core chưa hỗ trợ đổi quyền giữa tháng; trường hợp đó bị từ chối rõ ràng. Nhân sự đã có lịch không bị xóa vật lý.
- Chỉ một job Solver hoạt động trên workspace tại một thời điểm; khóa revision toàn CSDL hơi bảo thủ, có thể đánh STALE khi sửa một tháng khác. Hủy tiến trình riêng an toàn; chỉ giữ các phiên bản đã kiểm tra và commit, không cố giữ nghiệm giữa một lần CP-SAT chưa hoàn thành kiểm tra.
- Log và trạng thái có thật, không tạo phần trăm tiến độ giả. FRESH/RESUME chưa có callback tiến trình cho từng nghiệm bên trong tầng đang giải; chi tiết điểm/lượt/ALNS được ghi sau khi tầng trả kết quả.
- Giao diện đã build, type-check và kiểm tra component. **Chưa xác nhận trực quan/E2E bằng trình duyệt**: browser host không khả dụng; Computer Use Windows sau đó dừng vì không xác định được URL đủ chắc chắn để thi hành chính sách. Không đánh đồng component/API test với kiểm thử trình duyệt.
- Chưa kiểm tra render Excel desktop. Không coi kết quả INCOMPLETE hiện tại là lịch chính thức.

Do các giới hạn trên, đây là bản 5.0 có thể chạy và kiểm thử cục bộ, **chưa nghiệm thu trọn bộ mọi yêu cầu A–J**. Không có chức năng giả để che các phần còn thiếu.
