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

FRESH không liên kết/RESUME gọi engine 4.9 với checkpoint trong `data/jobs/<job-id>/`. Chứng minh phải qua fingerprint 4.9, đối chiếu điểm và kiểm tra nghiệm; đổi source ứng dụng làm proof prefix của bản DB về 0. REPAIR và FRESH liên kết không dùng prefix RESUME, không khóa điểm chất lượng baseline và không truy cập checkpoint gốc.

Vector REPAIR có 8 tầng: R4 chứa hai thành phần; R8 chứa chín vector điểm tháng. Từng thành phần là một lần tối ưu có giới hạn thời gian và được khóa trước tầng sau. Independent Validator tính lại mã công, nghỉ ngắn, số ô/người, V0 và chất lượng; sai điểm bị từ chối. Proof R1 chỉ gắn với toàn phạm vi cho phép hoặc cận 0 đã đạt, tách biệt với Validator INCOMPLETE.

## Ranh giới tháng

`repair_balance_context.py` chụp ledger gọn của các tháng có lịch trong những năm được Repair, cùng revision của job: ID phiên bản, số ca/đêm, ngày có dữ liệu và trọng số. Snapshot tháng gốc vẫn là nguồn của requests/rules/locks. Thay đổi đầu vào hoặc lịch ở tháng thống kê ngoài vùng cũng làm revision đổi và chặn lưu/duyệt stale.

`repair_balance.py` áp dụng sau Repair một tháng hoặc liên tháng. Mô hình LinkedModel mỏng không dựng R8, dùng nghiệm Repair đã kiểm tra làm warm start đầy đủ, đặt cap số ô bằng min(mốc + extra, max_changes) và giữ cận R4/R5 của mốc. Các tổng ca ngoài vùng là hằng số; tổng ca đêm được suy từ coverage cố định và tổng SHIFT chỉ thay đổi theo hỗ trợ ADMIN. Giảm việc đưa lại hàng trăm x vào mỗi biểu thức độ lệch. Bộ đếm độc lập đối chiếu mọi thành phần CP và kiểm tra lại toàn tháng/biên trước khi nhận. Presolve cho lượt tối ưu ngắn được tắt để ghi nhận ngay incumbent đầy đủ; thời gian chia cho các tầng công bằng, theo cùng deadline tổng.

Worker dành 65% cho sửa ổn định và tối đa 35% cho cân; fallback không đổi lịch khi lượt cân không cải thiện hoặc hết thời gian. Phạm vi tháng không tự mở thêm ở lượt cân. `stable_repair_proof` lưu riêng chứng minh mốc; không giữ các tầng R2–R8 của mốc như chứng minh cho phương án đã cân. Top-level R1 chỉ giữ khi số ô cuối bằng số ô mốc đã chứng minh. Kết quả và validation chứa `repair_balance`, hiển thị bằng `RepairBalanceSummary.vue`.

Chiều dài lịch sử là max(6, giới hạn đêm, giới hạn ngày trực, lookback giờ ca). Loader Excel cũ vẫn giữ quy ước gốc 5 ngày ở dữ liệu hiện tại để không đổi fingerprint; tầng 5.0 báo riêng E05 chưa đủ lịch sử. ANNUAL yêu cầu đủ lịch sử từ đầu và truyền mã đầy đủ giữa các tháng. Validator biên đọc toàn tháng sau với lịch sử thay từ tháng trước dự kiến, kiểm tra A07, B01–B04, E04/E05 và báo pending.

REPAIR một tháng kiểm tra cả tháng, tháng trước đã có và tháng sau đã có. `cascade: true` bật CP-SAT ghép nhiều tháng cho FRESH/REPAIR. `cascade_model.py` dùng chung CpModel, tái sử dụng các builder nghiệp vụ tháng hiện hành; A07, chuỗi B01–B04 và mục tiêu E04/E05 dùng biến của tháng trước thay cho lịch sử hằng. Mỗi vùng có biên phải cố định bằng lịch đã xác nhận và snapshot cũ của tháng ngoài vùng. Không suy diễn OFF cho lịch sử thiếu.

`cascade.py` mở vùng từ tháng gốc đến số tháng được phép; kiểm tra độc lập mọi nghiệm, toàn tháng và biên phải. Các vector mã đầy đủ, nghỉ ngắn mới theo lịch sử trước/sau thực tế và chất lượng đều được tính lại ngoài Solver. FRESH chỉ phạt thay đổi ở tháng sau; REPAIR phạt cả tháng gốc. Số tháng đổi là tiêu chí phụ sau R1. FAST dừng vùng khả thi đầu tiên; OPTIMAL mở rộng trong ngân sách. Chứng minh R1 tách phạm vi, cận 0 và chứng nhận toàn horizon; không suy ra tối ưu chất lượng. Thread giám sát hủy gọi stop_search trong lúc CP-SAT đang giải.

`cascade_service.py` chụp các snapshot cùng revision và frontier, chỉ lấy chuỗi tháng có lịch áp dụng liên tiếp. `cascade_worker.py` không giữ transaction trong lúc giải; lưu tất cả bản nháp trong một transaction sau khi kiểm tra revision, trạng thái job và toàn bộ current pointers. Snapshot kết quả chứa previous từ phương án liên kết. Duyệt gói kiểm tra lại toàn bộ mã, thời điểm ca, locks, revision, status, pointers và frontier, rồi thay current pointers đồng thời. Chặn duyệt hoặc sửa riêng thành viên DRAFT. V0 không thay đổi.

## Cân bằng năm

`year_metrics.py` không nhập Solver, đếm tổng ca SHIFT, ca T7/CN kể cả lễ và cuối tuần nghỉ trọn từ toàn bộ mã năm, lịch sử và frontier. Các cuối tuần có Chủ nhật cuối tháng vẫn kiểm tra ca 1 thứ Hai tháng sau. Trọng số tổng ca và ca cuối tuần là tổng ngày khả dụng × tỷ lệ công chuẩn × 1000, làm tròn từng tháng rồi rút gọn bằng gcd. Cuối tuần nghỉ trọn có trọng số theo số cơ hội hợp lệ. Thiếu dữ liệu ở biên được ghi pending.

Với tổng trọng số W, trọng số người i là w_i và tổng ca T, độ lệch là |W × ca_i − w_i × T|. Vector cân năm giảm lần lượt: tổng công vượt SHIFT, tổng hỗ trợ ADMIN, max/sum độ lệch tổng ca, max/sum độ lệch ca cuối tuần, thiếu hụt tỷ lệ nghỉ trọn thấp nhất, max/sum độ lệch nghỉ trọn, âm tổng nghỉ trọn, tổng chênh công tháng, số ô đổi. Trọng số không phụ thuộc lịch đang xếp nên không làm thay đổi mục tiêu để làm đẹp điểm. Vai trò phụ trách được bảo vệ bằng rule cứng, không chia nhỏ nhóm cân tổng ca theo vai trò. CT/DT/T không tính là ca trực.

`year_balance.py` giữ mã hiện tại làm warm start, dùng sổ đếm tăng dần để loại nhanh đề xuất không cải thiện. Chuyển một ngày, hai ngày cuối tuần/ngày thường và cụm tuần chỉ sửa SHIFT, giữ nguyên số người mỗi ca theo ngày. Rule quyền, requests, giờ bắt đầu và đóng băng được xét trước; các bộ kiểm tra gốc kiểm tra người, tháng và biên bị ảnh hưởng. Vector tăng dần được đối chiếu lại từ toàn năm.

`year_balance_cp.py` dùng LinkedModel với `quality=False, repair_terms=False`, thêm biểu thức năm gồm phần cố định ngoài vùng và phần biến trong vùng. CP tối ưu tuần tự, khóa các cận 0 và cận chia đều đã đạt khi hợp lệ; dùng cận miền tính từ số ngày/người/trọng số thay cho miền quá rộng. Khi tầng hiện tại hòa điểm, so tiếp toàn vector và giữ incumbent nếu nghiệm mới xấu hơn ở tầng sau. Tổng số cuối tuần nghỉ trọn không được giảm so với baseline, áp dụng trong cả exchange và CP. Các nghiệm được Independent Validator kiểm tra cả năm; thiếu mô hình giờ phi ca không được giải quyết bằng cách chấp nhận lỗi. Chứng minh OPTIMAL của một vùng không được quảng bá thành tối ưu toàn năm.

`year_service.py` chụp tất cả tháng hiện có của kế hoạch và frontier với một revision. BALANCE dùng đường lưu và duyệt nguyên gói của cascade, source BALANCE, giữ V0. Lịch ACTUAL/PUBLISHED, ngày đã bắt đầu, locks và phần trước `balance_from_month` cố định. Pending APPROVED phải REPAIR trước. ANNUAL mặc định dành 40% tổng ngân sách cho cùng bộ cân năm sau bước dựng lịch và trước transaction lưu V0; reset proof tháng sau khi thay mã và ghi riêng before/after. Cách cân cũ trong annual_fairness.py chỉ dùng khi explicit `annual_balance: false`.

## Clear năm

`year_clear.py` lấy phạm vi tất cả PlanningMonth/AnnualPlan cùng năm, kiểm tra revision và các trạng thái được bảo vệ. Transaction đặt SQLite write reservation trước online backup để snapshot sao lưu khớp trạng thái sắp dọn. Chỉ sau khi backup thành công mới tăng revision, bỏ current/baseline pointers, mở khóa kế hoạch, bỏ locks/lịch sử dẫn xuất đang được dựng lại và chuyển APPLIED về APPROVED kèm RequestRevision. Giữ nguyên lịch sử trước tháng đầu, mọi bảng input và các bảng schedule/snapshot bất biến; không bỏ trigger. Audit lưu pointers cũ, IDs lịch, job và đường dẫn backup. Lần Clear tiếp theo nhận diện tập ID đã lưu trữ để trả no-op khi năm vẫn trống.

Job SUCCEEDED thuộc năm hoặc gói nháp có result.version_ids chạm năm được lưu ARCHIVED; job năm khác chỉ có snapshot tham chiếu năm này vẫn giữ nguyên. Revision mới chặn duyệt bản nháp cũ. Giao diện xem preview rồi mới gửi year/revision; sau Clear tải calendar không chỉ định lịch cũ và cho phép chạy ANNUAL lại. Worker ANNUAL luôn đối chiếu frontier đã áp dụng sau tháng cuối trước transaction lưu, gồm cả đường không chạy cân năm.

## API

- `/api/import`, `/api/health`, `/api/months/{id}/calendar`.
- `/api/employees`, requests/batch/status/revisions, preferences, locks.
- `/api/months/{id}/rules`, configuration, validation-work-policy (chỉ Validator).
- `/api/jobs`, job detail, cancel, events (SSE), atomic linked-job confirm.
- `/api/versions/compare`, validate, snapshot, edit, restore, confirm, export.
- `/api/annual-plans`, plan statistics, atomic V0 confirm, annual/personal export.
- `/api/audit`.

Pydantic từ chối trường lạ và dữ liệu sai kiểu/range. Lỗi nghiệp vụ 422, xung đột revision/immutable 409. Mọi write có Origin ngoài localhost được từ chối; TrustedHost hạn chế host, CORS chỉ các origin localhost được chỉ định. Đây là ứng dụng một người quản lý cục bộ, chưa phải hệ thống xác thực nhiều người hoặc dịch vụ public.

## Tài liệu kỹ thuật đã đối chiếu

[SQLAlchemy 2 SQLite](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html), [FastAPI background execution](https://fastapi.tiangolo.com/tutorial/background-tasks/), [OR-Tools CP-SAT statuses](https://developers.google.com/optimization/cp/cp_solver), [Node.js official downloads](https://nodejs.org/en/download).
