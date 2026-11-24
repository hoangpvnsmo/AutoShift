# AutoShift 4.8 — Clean Release

Bản source hoàn chỉnh, tái cấu trúc từ chính thư mục AutoShift 4.7.5 người dùng gửi. Không phải patch. Không bổ sung hoặc thay đổi rule nghiệp vụ, thứ tự ưu tiên hay chiến lược tối ưu. Chưa phải phiên bản ban hành lịch chính thức.

## 1. Cài đặt và chạy (Windows / VS Code)

Giải nén thành một thư mục riêng, ví dụ `C:\AutoShift_4_8_clean`. Giữ nguyên thư mục `C:\AutoShift` cũ để đối chứng.

```powershell
cd C:\AutoShift_4_8_clean
& 'C:\Program Files\Python314\python.exe' -m pip show ortools openpyxl
# Chỉ cài nếu thiếu hoặc không đáp ứng requirements.txt:
& 'C:\Program Files\Python314\python.exe' -m pip install -r requirements.txt
& 'C:\Program Files\Python314\python.exe' run_autoshift.py
```

Nếu `python` đã trỏ đúng môi trường thì có thể dùng `python run_autoshift.py`. Không cần `--fresh` khi chạy lần đầu bản sạch. Chương trình dùng lại checkpoint được đóng gói, kiểm tra lịch bằng mô hình cố định trước khi khóa các điểm đã chứng minh. Nếu không khớp dữ liệu/source, phải chứng minh lại; vẫn có thể dùng lịch cũ làm hint, hoặc dự phòng khi đã xác thực.

## 2. Dữ liệu được giữ nguyên

Ba file làm việc sau là bản sao nguyên byte từ AutoShift.rar (không ghi đè dữ liệu thật trên máy bạn):

- `input/input.xlsx`: 21 nhân sự, 16 SHIFT và 5 ADMIN; tháng 12/2026; các đăng ký CT/DT/BKD, lịch sử và bốn nguyện vọng mềm như file gốc.
- `config/rules.json`: cấu hình hiện tại, gồm giờ ba ca và ngưỡng nghỉ tối thiểu 8 giờ; không đổi.
- `output/checkpoints/2026-12_real.json`: 651 ô phân công, E05 = (9, 1, 2, 2), E06 = (1,), prefix = 7/9.

Nếu sau lúc nén RAR bạn đã sửa dữ liệu, hãy copy bản cập nhật mới nhất của ba file trên từ thư mục cũ sang thư mục mới trước khi chạy. Không sửa tay checkpoint hoặc `release_manifest.json`. Checkpoint được lưu atomically sau khi kiểm tra kết quả; bản cũ không được dùng làm dự phòng nếu không hợp lệ với dữ liệu mới.

## 3. Một điểm chạy chính

| Nhu cầu | Lệnh |
|---|---|
| Tiếp tục tối ưu (RESUME) | `python run_autoshift.py` |
| Giải mới (FRESH) | `python run_autoshift.py --fresh` |
| Tăng ngân sách E05 | `python run_autoshift.py --rotation-time 120` |
| Giả lập công chuẩn SHIFT = 17 | `python run_autoshift.py --demo-support 17` |
| Giả lập thêm ngày lễ trong tháng | `python run_autoshift.py --demo-holidays 2026-12-25 2026-12-26` |
| LNS cố định | `python run_autoshift.py --fixed-lns` |
| Chỉ giải toàn cục | `python run_autoshift.py --no-lns` |
| Seed vùng tìm kiếm | `python run_autoshift.py --lns-seed 123` |
| Xem tham số đầy đủ | `python run_autoshift.py --help` |

Các demo có checkpoint riêng. FRESH không dùng nghiệm cũ để giải, nhưng không ghi đè checkpoint hợp lệ tốt hơn. RESUME so sánh vector mục tiêu theo thứ tự ưu tiên; E06 có thể xấu đi khi E05 tốt hơn. Không kết luận vô nghiệm từ UNKNOWN hoặc INFEASIBLE của một vùng LNS.

## 4. Cấu trúc

```text
AutoShift_4_8_clean/
  core/
    data/          # Đọc Excel/JSON, kiểu dữ liệu, lịch
    model/         # Biến quyết định, dựng mô hình
    constraints/   # Rule cứng A/B/C/D
    objectives/    # Các mục tiêu và hàm tính điểm
    optimization/  # CP-SAT, LNS/ALNS, khóa điểm, cận
    engine/        # Điều phối, cấu hình, checkpoint
    validation/    # Các kiểm tra hiện có (chưa là Validator giai đoạn 5)
    reporting/     # Dữ liệu kết quả và báo cáo terminal
    legacy/        # Ba module lịch sử còn dùng trong regression test
  input/
  config/
  output/checkpoints/
  tests/fixtures/  # Dữ liệu test cố định, độc lập file làm việc
  docs/
  run_autoshift.py
  release_manifest.json
  requirements.txt
```

API cho giao diện sau này: `core.engine.autoshift_engine.run_schedule(...)`, trả `EngineResult`. Đọc thêm `docs/ARCHITECTURE.md` và `docs/AUDIT_4_8.md`.

## 5. Kiểm thử

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s tests -p test_clean_release.py -v
```

Kết quả trong môi trường tạo bản release: 253 test, 166 PASS, 87 SKIP do không có OR-Tools. Đã kiểm tra cú pháp, đường dẫn import, migration chính checkpoint người dùng và tính lại chín tầng điểm. Chưa chạy được end-to-end CP-SAT ở môi trường này; cần chạy trên máy Windows đang có OR-Tools. Test lấy dữ liệu cố định ở `tests/fixtures`, không dùng Excel làm việc.

## 6. Điều kiện sử dụng

Đây là bản refactor, không phải bản xác nhận tuân thủ toàn bộ quy định nghiệp vụ. Chưa có Validator độc lập hoàn chỉnh, kiểm tra thời gian làm CT/DT/T/BKD, REPAIR hạn chế thay đổi lịch, xuất Excel/giao diện hoặc quản lý nhiều tiến trình cùng ghi checkpoint. Giữ nguyên cảnh báo không dùng làm lịch chính thức. Chỉ thay thư mục cũ sau khi kiểm thử đối chứng thành công.
# AutoShift 5.0 — web cục bộ

Đã bổ sung lớp SQLite, API, REPAIR, kế hoạch năm và giao diện Vue, giữ nguyên core/CLI 4.9. Chạy `start_autoshift.ps1` sau khi thiết lập bằng `setup_autoshift.ps1`. Web mặc định: http://127.0.0.1:8765.

Hướng dẫn, kết quả kiểm thử và giới hạn chức năng: [docs/AUTOSHIFT_5_0.md](docs/AUTOSHIFT_5_0.md). Bản này chưa nghiệm thu trọn bộ các trường hợp REPAIR xuyên tháng; xem giới hạn trước khi dùng lịch chính thức. Nội dung bản 4.9 bên dưới được giữ để tương thích.
