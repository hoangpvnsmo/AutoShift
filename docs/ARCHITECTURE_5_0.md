# Kiến trúc và schema AutoShift 5.0

## Phụ thuộc

```mermaid
flowchart LR
    V[Vue 3 + TypeScript] --> A[FastAPI / Pydantic]
    A --> S[Application Services]
    S --> R[Repository / Database Loader]
    R --> DB[(SQLite / SQLAlchemy 2)]
    S --> J[Owned worker process]
    J --> D[MonthData + immutable input snapshot]
    D --> C[Existing 4.9 Core]
    D --> P[Separate REPAIR CP-SAT]
    C --> IV[Independent full-code Validator]
    P --> IV
    IV --> TX[Revision check + short transaction]
    TX --> DB
    DB --> X[Existing Excel template + metadata]
```

Engine chỉ nhận dữ liệu trong bộ nhớ. Không có SQL/HTTP trong quá trình xây/giải ràng buộc. Worker đọc snapshot, đóng session trước khi giải; log dùng transaction ngắn riêng. Trước khi lưu kiểm tra revision toàn workspace và con trỏ lịch áp dụng. Solver không cập nhật con trỏ đó. Duyệt là một use case riêng.

`core/`, `validator/`, `run_autoshift.py`, `release_manifest.json`, `input/input.xlsx`, `config/rules.json` và các checkpoint/lịch gốc được giữ nguyên. Hash kế thừa không được đổi. Source 5.0 được lưu riêng trong snapshot, proof cũ không được nhập ngầm.

## Quan hệ chính

```mermaid
erDiagram
    employees ||--o{ employee_permissions : rights_by_period
    employees ||--o{ employee_monthly_settings : membership
    planning_months ||--o{ employee_monthly_settings : roster
    annual_plans ||--o{ planning_months : contains
    rule_profiles ||--o{ planning_months : configured_by
    planning_months ||--o{ holidays : calendar
    planning_months ||--o{ staffing_demands : exact_demand
    planning_months ||--o{ historical_assignments : explicit_previous
    employees ||--o{ requests : makes
    requests ||--o{ request_revisions : revision_history
    employees ||--o{ preferences : weighted_soft
    planning_months ||--o{ input_snapshots : captures
    input_snapshots ||--o{ schedule_versions : reproducible_input
    planning_months ||--o{ schedule_versions : versions
    schedule_versions ||--o{ schedule_assignments : full_codes
    schedule_versions ||--o{ schedule_versions : parent_and_baseline
    employees ||--o{ schedule_assignments : assigned
    work_codes ||--o{ schedule_assignments : valid_code
    work_codes ||--o{ shift_types : catalogue
    planning_months ||--o{ schedule_locks : cell_locks
    schedule_versions ||--o{ schedule_changes : cell_diff
    schedule_versions ||--o{ objective_scores : ordered_vectors
    schedule_versions ||--o{ validation_results : independent_reports
    planning_months ||--o{ solver_runs : durable_jobs
    annual_plans ||--o{ solver_runs : rolling_jobs
    planning_months ||--o{ import_records : idempotent_sources
```

`input_state` chứa revision giao dịch; `audit_logs` lưu thao tác nghiệp vụ. CSDL không chứa lịch dưới dạng một chuỗi JSON: mỗi assignment là khóa `(version_id, employee_id, day)`, code là FK tới work_codes và có CHECK loại trừ 0. JSON chỉ dùng cho input snapshot, cấu hình có version, report và vector mục tiêu.

Schema DDL đầy đủ được tạo tại `docs/schema-5.0.sql`. Migration là `migrations/versions/0001_autoshift5.py`; `initialize` cài cùng các trigger trong test và khi startup. Foreign keys, unique tháng, unique active request/người/ngày, unique active job và index ngày/nhân sự được bật.

Assignments, snapshots, rule profiles và schedule_changes không UPDATE/DELETE. Versions PLANNED/PUBLISHED/ACTUAL không UPDATE; không version nào được DELETE. Không thêm assignment vào phiên bản đã xác nhận. Để xây lịch import, transaction tạo DRAFT, ghi đủ assignments/report, rồi chuyển PLANNED trước commit. Mọi thay đổi sau đó tạo version con.

## Snapshot và proof

Snapshot chứa đủ MonthData, preferences, giờ phi ca cho Validator, source hashes, phi?n b?n Python/th? vi?n, revision, baseline/current version, danh sách yêu cầu, locks và toàn bộ tùy chọn job. Full baseline là những assignment bất biến được tham chiếu bởi ID version. Loader mới ưu tiên lịch trước tháng đã xác nhận khi nó tồn tại; snapshot lịch sử không bị thay đổi.

FRESH/RESUME gọi engine 4.9 với checkpoint trong `data/jobs/<job-id>/`. Chứng minh phải qua fingerprint 4.9, đối chiếu điểm và kiểm tra nghiệm; đổi source ứng dụng làm proof prefix của bản DB về 0. REPAIR không dùng prefix RESUME, không khóa điểm chất lượng baseline và không truy cập checkpoint gốc.

Vector REPAIR có 8 tầng: R4 chứa hai thành phần; R8 chứa chín vector điểm tháng. Từng thành phần là một lần tối ưu có giới hạn thời gian và được khóa trước tầng sau. Independent Validator tính lại mã công, nghỉ ngắn, số ô/người, V0 và chất lượng; sai điểm bị từ chối. Proof R1 chỉ gắn với toàn phạm vi cho phép hoặc cận 0 đã đạt, tách biệt với Validator INCOMPLETE.

## Ranh giới tháng

Chiều dài lịch sử là max(6, giới hạn đêm, giới hạn ngày trực, lookback giờ ca). Loader Excel cũ vẫn giữ quy ước gốc 5 ngày ở dữ liệu hiện tại để không đổi fingerprint; tầng 5.0 báo riêng E05 chưa đủ lịch sử. ANNUAL yêu cầu đủ lịch sử từ đầu và truyền mã đầy đủ giữa các tháng. Validator biên đọc toàn tháng sau với lịch sử thay từ tháng trước dự kiến, kiểm tra A07, B01–B04, E04/E05 và báo pending.

REPAIR một tháng kiểm tra cả tháng, tháng trước đã có và tháng sau đã có. Nếu cần đổi cả hai tháng để thỏa rule, từ chối rõ ràng vì chưa có mô hình sửa liên kết. Không tự lấy OFF hoặc bỏ các ràng buộc để vượt qua biên.

## API

- `/api/import`, `/api/health`, `/api/months/{id}/calendar`.
- `/api/employees`, requests/batch/status/revisions, preferences, locks.
- `/api/months/{id}/rules`, configuration, validation-work-policy (chỉ Validator).
- `/api/jobs`, job detail, cancel, events (SSE).
- `/api/versions/compare`, validate, snapshot, edit, restore, confirm, export.
- `/api/annual-plans`, plan statistics, atomic V0 confirm, annual/personal export.
- `/api/audit`.

Pydantic từ chối trường lạ và dữ liệu sai kiểu/range. Lỗi nghiệp vụ 422, xung đột revision/immutable 409. Mọi write có Origin ngoài localhost được từ chối; TrustedHost hạn chế host, CORS chỉ các origin localhost được chỉ định. Đây là ứng dụng một người quản lý cục bộ, chưa phải hệ thống xác thực nhiều người hoặc dịch vụ public.

## Tài liệu kỹ thuật đã đối chiếu

[SQLAlchemy 2 SQLite](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html), [FastAPI background execution](https://fastapi.tiangolo.com/tutorial/background-tasks/), [OR-Tools CP-SAT statuses](https://developers.google.com/optimization/cp/cp_solver), [Node.js official downloads](https://nodejs.org/en/download).
