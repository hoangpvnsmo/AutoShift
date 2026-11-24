# Biên bản rà soát kỹ thuật 4.8

Nguồn: AutoShift.rar người dùng gửi; giải nén 149 file, bao gồm cache Python. Kiểm tra trực tiếp mã nguồn, cấu hình, workbook, checkpoint và test.

## Thay đổi

- Từ 19 file Python chạy ở thư mục gốc (gồm main.py và runner theo từng bước) còn 1 entry point. Runner cũ không cần trong bản mới; giữ RAR/thư mục cũ để đối chứng.
- 47 module nghiệp vụ được bố trí lại theo package (không tính __init__.py). Trong đó 45 module giữ AST tương đương sau khi chuẩn hóa import, 2 module checkpoint thay đổi để hỗ trợ cấu trúc mới. Loại khỏi release 2 bộ điều phối cũ không còn được import bởi runtime hoặc test: objective_resume_alns.py và objective_resume_lns.py. Ba module lịch sử còn được test sử dụng nằm trong core/legacy. Không xóa các helper cũ mà engine hiện hành vẫn gọi.
- Không thay đổi biểu thức ràng buộc/mục tiêu, ngân sách mặc định, thứ tự tầng hay thuật toán tìm kiếm; di chuyển package và cập nhật import/mocks/test. Không thực hiện gộp các hàm có khả năng khác nhau về nghiệp vụ để tránh thay đổi hành vi đã kiểm thử.
- requirements.txt trước đây chỉ có openpyxl; bổ sung ortools>=9.14,<10 (dải dự kiến hỗ trợ API hiện có, chưa xác nhận toàn bộ phiên bản). Dùng môi trường OR-Tools đang chạy tốt trên máy người dùng nếu đáp ứng.
- README cũ chỉ mô tả giai đoạn 1; thay bằng hướng dẫn bản 4.8. Loại cache, cài đặt VS Code cá nhân; bổ sung .gitignore.
- Test cảnh báo thiếu giờ ca cũ thất bại vì dữ liệu người dùng đã khai giờ. Sửa test để tạo tình huống thiếu giờ tường minh. Cố định dữ liệu test riêng; thêm 5 test regression bản sạch.

## Checkpoint

File JSON gốc còn nguyên. Mã hash 4.7.5 tính từ source gốc và dữ liệu trùng chính xác base_hash đã lưu; pref_hash cũng trùng. Release dùng manifest chứa hash của source gốc và source mới để tái tạo hash lịch sử 4.7.2–4.7.5. Chỉ migration khi toàn bộ source Python trong core và entry point khớp manifest, sau đó vẫn kiểm tra tính hợp lệ bằng mô hình và so điểm. Hash mới bao gồm module trong thư mục con và manifest. Không coi checksum/manifest là chữ ký an ninh hay bằng chứng tối ưu tự thân; đây là cơ chế tương thích trong bộ dữ liệu và source tin cậy.

Đối chiếu thực tế không dùng solver: 651 ô (21 × 31); 279 lượt trực; tổng công SHIFT 284; 0 hỗ trợ ADMIN. Cả chín vector điểm tính lại trùng checkpoint: ((0,0,1,96,0),(1,3),(1,1),(0,0),(1,128),(0,0,0),(0,40,1,0),(9,1,2,2),(1,)). Prefix đủ điều kiện migration 7/9, vẫn phải qua kiểm tra CP-SAT lúc chạy. File checkpoint không bị ghi lại trong quá trình kiểm tra.

## Test

- Bản gốc: 248 test, 160 PASS, 1 FAIL (cảnh báo giờ), 87 SKIP.
- Bản sạch: 253 test, 166 PASS, 0 FAIL, 87 SKIP. Bao gồm 5 test mới về checkpoint thực tế, đổi hard/soft input, đổi source nested và duy nhất một CLI.
- Python 3.13.5 + openpyxl 3.1.5; không có OR-Tools. Chưa xác nhận solve end-to-end, runtime API của OR-Tools hoặc benchmark tính toán trên môi trường này.
- Compileall PASS; import nội bộ kiểm tra tĩnh đầy đủ; so AST sau chuẩn hóa import PASS (45 module); dữ liệu làm việc và checkpoint so byte PASS.

## Các điểm còn tồn tại (không tự sửa nghiệp vụ trong refactor)

1. Giờ làm CT/DT/T/BKD chưa được mô hình hóa đầy đủ; kiểm tra hiện hành không đủ để phê duyệt lịch chính thức.
2. E05 chưa chứng minh tối ưu toàn cục; E06 còn 1 nguyện vọng chưa đạt. Kết quả tốt hơn tích lũy qua nhiều lần chạy không chứng minh ALNS luôn tốt hơn các phương pháp khác.
3. JSON có prefer_staffing, minimize_admin_support, balance_admin_support, minimize_schedule_changes nhưng chúng chưa là các công tắc độc lập trong registry đang chạy. Stage 3 luôn tối ưu các thành phần công/hỗ trợ ADMIN; chưa có mục tiêu hạn chế đổi lịch (REPAIR). Giữ nguyên khóa cấu hình để tương thích hash, không coi việc bật chúng là đã triển khai chức năng mới.
4. Checkpoint ghi nguyên tử nhưng chưa có khóa nhiều tiến trình; chạy một tiến trình trên một checkpoint.
5. Validator độc lập giai đoạn 5, REPAIR, Excel output và UI chưa hoàn thiện. Thư mục validation hiện chỉ chứa các kiểm tra đã có.

## Đối chứng trên Windows

Giải nén mới, chạy run_autoshift.py với nguyên input/config/checkpoint, không --fresh. Mong đợi log `Checkpoint 4.7.5: hash nền khớp`, xác thực lịch cũ, tái sử dụng 7 tầng, điểm 7 tầng đầu giữ nguyên, E05/E06 có vector từ điển không kém checkpoint hợp lệ. Chạy toàn bộ unittest trên máy có OR-Tools; các test solver không được skip vì thiếu thư viện. Nếu phát sinh lỗi, giữ thư mục cũ và gửi log. Không thay/xóa thư mục cũ trước khi đối chứng thành công.
