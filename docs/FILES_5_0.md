# File m?i v? s?a cho AutoShift 5.0

Repair có cân bằng: `autoshift5/repair_balance_context.py` chụp số ca ngoài vùng; `repair_balance.py` hiệu chỉnh có cap ô đổi/nghỉ và kiểm tra độc lập. Tích hợp trong cả `worker.py` và `cascade_worker.py`, hai đường enqueue, schema và `App.vue`. Component `RepairBalanceSummary.vue` và tests; regression `tests/test_repair_balance.py`.

Clear năm: `autoshift5/year_clear.py` (preview, backup và reset logic giữ lịch sử), DTO/routes trong `schemas.py`/`api.py`, kiểm tra biên sau ANNUAL trong `worker.py`, `frontend/src/components/ClearYearDialog.vue` và tích hợp trong `App.vue`. Test: `tests/test_year_clear.py`, `ClearYearDialog.test.ts`; hợp đồng `docs/openapi-5.0.json` được cập nhật.

Gi? nguy?n core/, validator/, CLI, input, rules v? l?ch/checkpoint g?c.

| File | Tr?ch nhi?m |
|---|---|
| `alembic.ini` | Migration SQLite v? trigger b?t bi?n |
| `autoshift5/__init__.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/__main__.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/annual.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/annual_fairness.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/api.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/bootstrap.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/db.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/domain.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/importer.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/jobs.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/models.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/quality_model.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/repair.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/reporting.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/repository.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/schemas.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/services.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/validation.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `autoshift5/worker.py` | Domain / repository / service / API / worker / Solver / reporting 5.0 |
| `docs/ARCHITECTURE_5_0.md` | T?i li?u / schema / OpenAPI / hash ??i ch?ng |
| `docs/AUTOSHIFT_5_0.md` | T?i li?u / schema / OpenAPI / hash ??i ch?ng |
| `docs/FILES_5_0.md` | T?i li?u / schema / OpenAPI / hash ??i ch?ng |
| `docs/TEST_REPORT_5_0.md` | T?i li?u / schema / OpenAPI / hash ??i ch?ng |
| `docs/openapi-5.0.json` | T?i li?u / schema / OpenAPI / hash ??i ch?ng |
| `docs/protected-files-sha256.json` | T?i li?u / schema / OpenAPI / hash ??i ch?ng |
| `docs/schema-5.0.sql` | T?i li?u / schema / OpenAPI / hash ??i ch?ng |
| `frontend/index.html` | Vue 3 + TypeScript, giao di?n v? component tests |
| `frontend/package-lock.json` | Vue 3 + TypeScript, giao di?n v? component tests |
| `frontend/package.json` | Vue 3 + TypeScript, giao di?n v? component tests |
| `frontend/src/App.vue` | Vue 3 + TypeScript, giao di?n v? component tests |
| `frontend/src/components/ScheduleGrid.test.ts` | Vue 3 + TypeScript, giao di?n v? component tests |
| `frontend/src/components/ScheduleGrid.vue` | Vue 3 + TypeScript, giao di?n v? component tests |
| `frontend/src/main.ts` | Vue 3 + TypeScript, giao di?n v? component tests |
| `frontend/src/store.ts` | Vue 3 + TypeScript, giao di?n v? component tests |
| `frontend/src/style.css` | Vue 3 + TypeScript, giao di?n v? component tests |
| `frontend/src/types.ts` | Vue 3 + TypeScript, giao di?n v? component tests |
| `frontend/tsconfig.json` | Vue 3 + TypeScript, giao di?n v? component tests |
| `frontend/vite.config.ts` | Vue 3 + TypeScript, giao di?n v? component tests |
| `migrations/env.py` | Migration SQLite v? trigger b?t bi?n |
| `migrations/versions/0001_autoshift5.py` | Migration SQLite v? trigger b?t bi?n |
| `requirements-web.txt` | Thi?t l?p Windows / dependency |
| `scripts/benchmark_annual.py` | Ch?y test, integration v? benchmark t?i l?p |
| `scripts/integration_december.py` | Ch?y test, integration v? benchmark t?i l?p |
| `scripts/run_tests.py` | Ch?y test, integration v? benchmark t?i l?p |
| `setup_autoshift.ps1` | Thi?t l?p Windows / dependency |
| `start_autoshift.bat` | Thi?t l?p Windows / dependency |
| `start_autoshift.ps1` | Thi?t l?p Windows / dependency |
| `stop_autoshift.ps1` | Thi?t l?p Windows / dependency |
| `tests/test_v50.py` | Regression v? integration c? CP-SAT th?t |
| `.gitignore` | Lo?i runtime, CSDL, build v? artifacts ki?m th? kh?i Git |
| `README.md` | H??ng d?n 5.0; gi? h??ng d?n c? b?n d??i |

Các file bổ sung cho sửa liên kết: `autoshift5/cascade_model.py` (mô hình ghép tháng), `cascade.py` (mở vùng và đối chiếu độc lập), `cascade_service.py` (snapshot và duyệt nguyên gói), `cascade_worker.py` (worker lưu nguyên gói), `tests/test_cascade.py` (CP-SAT và API integration), `scripts/check_cascade_copy.py` (kiểm tra trên bản sao CSDL).

Cân năm: `autoshift5/year_metrics.py` (đếm và điểm độc lập), `year_balance.py` (hiệu chỉnh có warm start), `year_balance_cp.py` (CP ghép vùng nhỏ), `year_service.py` (snapshot cho BALANCE), `tests/test_year_balance.py`, `frontend/src/components/AnnualBalanceSummary.vue` và test tương ứng. `scripts/run_year_balance_review.py` tạo gói DRAFT qua API, không duyệt.

Artifacts, CSDL v? build trong artifacts/, data/, frontend/dist/ kh?ng commit d? li?u v?n h?nh v?o Git.
