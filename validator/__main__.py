"""CLI độc lập: chỉ đọc dữ liệu, không gọi solver hoặc sửa checkpoint."""
import argparse, json
from zipfile import BadZipFile
from openpyxl.utils.exceptions import InvalidFileException
from .excel_schedule import read_schedule, write_schedule
from pathlib import Path
from core.data.data_loader import load_data
from core.engine.checkpoint import read_checkpoint
from . import RULES, validate_structure, checkpoint_cells
from .quality import validate_quality, score_vector
from core.objectives.preference_checks import load_preferences


def main(argv=None):
    parser=argparse.ArgumentParser(description='AutoShift Validator 4.9 - kiểm tra checkpoint hoặc lịch Excel.')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--catalog',action='store_true',help='In danh mục rule, không đọc dữ liệu.')
    source_group=parser.add_mutually_exclusive_group()
    source_group.add_argument('--schedule',type=Path,help='File .xlsx lịch thực tế, sheet Schedule.')
    source_group.add_argument('--checkpoint',type=Path,help='Checkpoint để kiểm tra cấu trúc (mặc định real của tháng).')
    parser.add_argument('--export-schedule',type=Path,help='Xuất bản lịch thử nghiệm từ checkpoint sang .xlsx mới; không ghi đè.')
    parser.add_argument('--export-report',type=Path,help='Xuất mẫu 6.1 đầy đủ; ngày nghỉ trống ở Lich truc, giữ OFF trong Schedule.')
    parser.add_argument('--structure-only',action='store_true',help='Chỉ kiểm tra cấu trúc như 5.1.')
    parser.add_argument('--work-time-policy',type=Path,help='JSON giờ/phân loại CT/DT/T/BKD đã được xác nhận; mặc định chưa khai báo.')
    parser.add_argument('--json',action='store_true',help='In báo cáo JSON ra stdout.')
    a=parser.parse_args(argv)
    if a.export_schedule and (a.schedule or a.catalog):
        parser.error('--export-schedule chỉ dùng với nguồn checkpoint.')
    if a.export_report and (a.catalog or a.structure_only or a.export_schedule): parser.error('--export-report cần kiểm tra đầy đủ; không dùng với --catalog/--structure-only/--export-schedule.')
    if a.catalog:
        if a.json:
            from dataclasses import asdict
            print(json.dumps([asdict(r) for r in RULES],ensure_ascii=False,indent=2))
        else:
            for r in RULES: print(f'{r.id:20} {r.kind.value:10} {r.coverage.value:15} {r.step:10} {r.title}')
        return 0
    try:
        data=load_data(a.root/'input/input.xlsx',a.root/'config/rules.json')
        expected=None; reference_note=None
        prefs=load_preferences(a.root/'input/input.xlsx',data)
        if a.schedule:
            cells=read_schedule(a.schedule,data)
            source='excel_schedule'
        else:
            path=a.checkpoint or a.root/'output/checkpoints'/f'{data.year:04d}-{data.month:02d}_real.json'
            cp=read_checkpoint(path)
            if cp is None: raise ValueError(f'Không tìm thấy checkpoint: {path}')
            cells=tuple(checkpoint_cells(data,cp.assignments))
            from core.engine.checkpoint import fingerprints
            if cp.base_hash==fingerprints(data,prefs,a.root)[0] and cp.pref_hash==fingerprints(data,prefs,a.root)[1]:
                expected=cp.scores
            else:
                reference_note='Checkpoint khác fingerprint/phiên bản hoặc nguyện vọng; tính lại điểm E05 4.9, không đối chiếu vector cũ.' 
            source='checkpoint_reconstructed'
        policy=json.loads(a.work_time_policy.read_text(encoding='utf-8-sig')) if a.work_time_policy else None
        report=(validate_structure(data,cells,source=source) if a.structure_only else
                validate_quality(data,cells,work_policy=policy,prefs=prefs,source=source,expected_scores=expected))
        if a.export_schedule:
            if a.structure_only or report.errors:
                raise ValueError('Chỉ xuất khi đã kiểm tra toàn bộ Validator và không phát hiện ERROR.')
            write_schedule(a.export_schedule,data,cells,source=source)
        if a.export_report:
            from core.reporting.excel_report import write_full_report
            write_full_report(a.export_report,data,{(i,d):c for i,d,c in cells},report)
    except (OSError,ValueError,TypeError,KeyError,IndexError,BadZipFile,InvalidFileException) as exc:
        if a.json: print(json.dumps({'verdict':'ERROR','input_error':str(exc)},ensure_ascii=False))
        else: print(f'ERROR: không thể đọc dữ liệu/checkpoint/Excel: {exc}')
        return 2
    if a.json:
        payload=report.to_dict()
        if a.export_schedule: payload['exported_schedule']=str(a.export_schedule)
        if a.export_report: payload['exported_report']=str(a.export_report)
        if reference_note: payload['reference_note']=reference_note
        print(json.dumps(payload,ensure_ascii=False,indent=2,default=str))
    else:
        print(f'AutoShift Independent Validator 4.9 | {report.month:02d}/{report.year}')
        print(f'Nguồn: Excel {a.schedule}; đọc mã gốc, không dựng lại từ Requests.' if a.schedule else 'Nguồn: checkpoint; mã phi ca dựng theo Requests hiện tại; chưa xác minh hash/chứng minh.')
        if reference_note: print(reference_note)
        if a.export_schedule: print(f'Đã xuất lịch thử nghiệm: {a.export_schedule}')
        if a.export_report: print(f'Đã xuất báo cáo Excel: {a.export_report}')
        for r in report.results:
            print(f'{r.rule_id:20} {r.status.value:15} {r.score if r.score is not None else ""} {r.note}')
            for f in r.findings: print(f'  {f.severity.value}: ID={f.employee_id} ngày={f.day} ca={f.shift} {f.message} | yêu cầu={f.expected} thực tế={f.actual} ngày liên quan={f.related_day}')
        print(f'Kết luận: {report.verdict} | ERROR={len(report.errors)} | hard pending={len(report.hard_pending)}')
        print('Vector chất lượng:',score_vector(report))
        print('Đối chiếu điểm không xác minh chứng minh/hash; kiểm tra độc lập ngoài solver; kết quả chỉ áp dụng dữ liệu/quy định đã kiểm tra. KHÔNG xác nhận lịch chính thức.')
    return 2 if report.verdict=='ERROR' else 3 if report.verdict=='INCOMPLETE' else 0

if __name__=='__main__': raise SystemExit(main())
