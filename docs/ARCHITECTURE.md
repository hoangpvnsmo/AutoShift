# Kiến trúc AutoShift 4.8

## Luồng chính

`run_autoshift.py` → `core.data.data_loader` + `core.objectives.preference_checks` → `core.engine.autoshift_engine.run_schedule` → `core.model.engine_model.build_stage3` → `core.engine.objective_engine.solve_objectives` → `core.reporting.schedule_report.collect_report` → `core.engine.engine_checkpoint.choose_checkpoint` → lưu checkpoint hợp lệ.

CLI phụ trách đường dẫn, tham số và demo; engine trả EngineResult. Các module kiểm tra không cần OR-Tools nằm ở validation và các hàm tính điểm ở objectives. Các thuật toán tìm kiếm nằm ở optimization. Module legacy giữ nguyên nhằm phục vụ test các phiên bản trước; không được gọi từ entry point chính theo đồ thị import tĩnh.

## Tầng tối ưu

| Thứ tự | Key | Mục tiêu | Ngân sách mặc định |
|---|---|---|---:|
| 1 | workforce | Tổng công, hỗ trợ ADMIN | 120s |
| 2 | e01 | Cân bằng loại ca | 120s |
| 3 | e02 | Cân bằng ca đêm | 120s |
| 4 | holiday | Trực lễ | 120s |
| 5 | weekend | Trực cuối tuần không trùng lễ | 120s |
| 6 | rest_a | Cụm nghỉ | 60s |
| 7 | rest_b | Cuối tuần nghỉ trọn | 60s |
| 8 | rotation | Chuyển loại ca | 60s |
| 9 | preferences | Nguyện vọng mềm | 30s |

Các ngân sách là giới hạn solver chứ không phải thời gian bắt buộc. Các tầng được khóa theo thứ tự từ điển. Tái sử dụng prefix chứng minh liên tiếp chỉ khi hash và điểm khớp, lịch được kiểm tra. E05/E06 có ALNS thích nghi và toàn cục; không dùng kết quả vô nghiệm của vùng LNS để kết luận tối ưu toàn cục.

## Đường dẫn module thường dùng

- `core/data/models.py`, `calendar_utils.py`, `data_loader.py`: dữ liệu đầu vào.
- `core/model/model_builder.py`, `engine_model.py`: mô hình và assembly Stage 3.
- `core/constraints/hard_constraints.py`, `credit_constraints.py`, `admin_constraints.py`: rule bắt buộc.
- `core/objectives/*_constraints.py`, `*_checks.py`, `rest_bound_v2.py`, `day_balance_v2.py`: biểu thức, hàm tính điểm, cận.
- `core/optimization/objective_alns.py`, `adaptive_policy.py`, `lns_neighborhood.py`: chiến lược tìm kiếm.
- `core/engine/engine_config.py`: danh sách tầng, ngân sách và tùy chọn.
- `core/engine/checkpoint.py`, `engine_checkpoint.py`: hash, đọc ghi, migration và so sánh nghiệm.
- `core/reporting/schedule_report.py`: tính lại điểm, tạo mã lịch và báo cáo.

Tên đường dẫn module đã đổi; các import cũ như `core.data_loader` không còn là interface của release này. Bảng ánh xạ đầy đủ trong release_manifest.json (`module_map`). Dùng import mới khi phát triển giao diện hoặc chức năng bổ sung.

## Phát triển tiếp

Giai đoạn 5: Validator độc lập nhận dữ liệu và lịch mã hóa đầy đủ, trả danh sách vi phạm có mã rule, ID/ngày/ca và mức độ. Không phụ thuộc trạng thái solver. Cần thống nhất giờ làm CT/DT/T/BKD trước khi kiểm tra lịch chính thức. Giai đoạn 6: xuất Excel và UI sử dụng EngineResult. REPAIR là bài toán riêng, cần ưu tiên giữ lịch đã công bố và khóa phần lịch không được sửa; chưa có trong 4.8.
