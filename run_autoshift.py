"""AutoShift: stable command-line entry point. Clean release."""
from pathlib import Path
from datetime import date
from dataclasses import replace
import argparse
from core.engine.engine_config import EngineOptions

ROOT=Path(__file__).resolve().parent

def build_parser():
    parser=argparse.ArgumentParser(description='AutoShift 4.9 – Flexible Rotation + Validator + Excel')
    parser.add_argument('--demo-support',type=int,metavar='N',help='Giả lập công chuẩn SHIFT = N, chỉ trong bộ nhớ; không sửa Excel/JSON.')
    parser.add_argument('--base-time',type=float,default=120,help='Giới hạn giây lượt giải nền.')
    parser.add_argument('--quality-time',type=float,default=120,help='Giới hạn giây lượt giải E01.')
    parser.add_argument('--night-time',type=float,default=120,help='Giới hạn giây lượt giải E02.')
    parser.add_argument('--holiday-time',type=float,default=120,help='Giới hạn giây lượt giải ngày lễ.')
    parser.add_argument('--weekend-time',type=float,default=120,help='Giới hạn giây lượt giải cuối tuần.')
    parser.add_argument('--demo-holidays',nargs='+',type=date.fromisoformat,metavar='YYYY-MM-DD',help='Thêm ngày lễ giả lập, chỉ trong bộ nhớ.')
    parser.add_argument('--ideal-time',type=float,default=20,help='Giây thử cận E03 (0 để tắt); nằm trong ngân sách mỗi tầng.')
    parser.add_argument('--rest-time',type=float,default=60,help='Giây tầng E04a.')
    parser.add_argument('--rest-weekend-time',type=float,default=60,help='Giây tầng E04b.')
    parser.add_argument('--rotation-time',type=float,default=60,help='Giây tầng E05.')
    parser.add_argument('--preference-time',type=float,default=30,help='Giây tầng E06.')
    parser.add_argument('--demo-preferences',action='store_true',help='Giả lập nguyện vọng khi sheet Preferences trống/không có.')
    parser.add_argument('--fresh',action='store_true',help='Không sử dụng lịch cũ để giải; chỉ lưu nếu không kém checkpoint hợp lệ.')
    parser.add_argument('--checkpoint-validation-time',type=float,default=10,help='Giây kiểm tra checkpoint bằng mô hình cố định.')
    parser.add_argument("--lns-fraction",type=float,default=.5,help="Phần ngân sách E05/E06 dành cho LNS (0..0.9).")
    parser.add_argument("--lns-slice",type=float,default=5.,help="Giây tối đa mỗi vùng LNS.")
    parser.add_argument("--lns-seed",type=int,default=None,help="Seed chọn vùng. Mặc định tự đổi mỗi lần chạy.")
    search=parser.add_mutually_exclusive_group()
    search.add_argument("--no-lns",action="store_true",help="Tắt LNS; dùng giải toàn cục.")
    search.add_argument("--fixed-lns",action="store_true",help="Dùng LNS phân bổ cố định như 4.7.3.")
    parser.add_argument("--lns-stall",type=int,default=5,help="Số vùng liên tiếp không cải thiện trước khi chuyển toàn cục (>=3).")
    parser.add_argument("--require-complete-validation",action="store_true",help="Không lưu nếu còn rule cứng NOT_CHECKED.")
    parser.add_argument("--work-time-policy",type=Path,help="JSON giờ phi ca đã xác nhận; chỉ kiểm tra, chưa đưa vào mô hình solver.")
    parser.add_argument("--no-excel",action="store_true",help="Không xuất Excel tự động.")
    parser.add_argument("--excel",type=Path,help="Đường dẫn Excel mới; không ghi đè file có sẵn.")
    return parser


def options_from_args(args):
    return EngineOptions(base_time=args.base_time,quality_time=args.quality_time,night_time=args.night_time,
        holiday_time=args.holiday_time,weekend_time=args.weekend_time,rest_time=args.rest_time,
        rest_weekend_time=args.rest_weekend_time,rotation_time=args.rotation_time,preference_time=args.preference_time,
        ideal_time=args.ideal_time,validation_time=args.checkpoint_validation_time,fresh=args.fresh,
        lns_fraction=0 if args.no_lns else args.lns_fraction,lns_slice=args.lns_slice,lns_seed=args.lns_seed,
        adaptive=not args.fixed_lns,stall_limit=args.lns_stall)


def main(argv=None):
    parser=build_parser(); args=parser.parse_args(argv)
    try: opts=options_from_args(args)
    except ValueError as exc: parser.error(str(exc))
    # Lazy import: --help and argument errors work even without OR-Tools.
    from core.data.data_loader import load_data
    from core.objectives.preference_checks import load_preferences,demo_preferences
    from core.engine.checkpoint import checkpoint_path
    from core.engine.autoshift_engine import run_schedule
    from core.reporting.schedule_report import print_layers,print_report
    data=load_data(ROOT/'input/input.xlsx',ROOT/'config/rules.json')
    if args.demo_holidays:
        if any((d.year,d.month)!=(data.year,data.month) for d in args.demo_holidays):
            parser.error('--demo-holidays phải thuộc tháng đang lập lịch.')
        data=replace(data,holidays=data.holidays | frozenset(args.demo_holidays))
        print('GIẢ LẬP: thêm ngày lễ', ', '.join(map(str,args.demo_holidays)), '| Không sửa Excel/JSON.')
    if args.demo_support is not None:
        if args.demo_support<0 or args.demo_support>31:
            parser.error('--demo-support phải trong 0..31')
        data=replace(data,employees=tuple(replace(e,target_credit=args.demo_support) if e.group=='SHIFT' else e for e in data.employees))
        print(f'GIẢ LẬP: công chuẩn riêng mỗi SHIFT = {args.demo_support}. Không sửa dữ liệu gốc.')
    prefs=load_preferences(ROOT/'input/input.xlsx',data)
    if args.demo_preferences:
        if prefs: parser.error('--demo-preferences chỉ dùng khi không có nguyện vọng thật.')
        prefs=demo_preferences(data)
        print('GIẢ LẬP: nguyện vọng mềm, không sửa Excel.')
    path=checkpoint_path(ROOT,data.year,data.month,args.demo_support,args.demo_holidays,args.demo_preferences)
    import json
    from validator.engine_gate import ValidationRejected
    try:
        work_policy=json.loads(args.work_time_policy.read_text(encoding='utf-8-sig')) if args.work_time_policy else None
        if args.work_time_policy and not isinstance(work_policy,dict):
            raise ValueError('Chính sách giờ phi ca phải là JSON object.')
    except (OSError,ValueError) as exc:
        parser.error(str(exc))
    if path.exists():
        backup=path.with_name(path.stem+"_before49.json")
        if not backup.exists():
            with backup.open("xb") as f: f.write(path.read_bytes())
    try:
        result=run_schedule(data,prefs,root=ROOT,options=opts,checkpoint_file=path,
                            work_policy=work_policy,require_complete=args.require_complete_validation)
    except ValidationRejected as exc:
        print('VALIDATION_REJECTED:',exc)
        if exc.report is not None:
            for f in exc.report.errors:
                print(f'  ERROR {f.rule_id}: ID={f.employee_id} ngày={f.day} ca={f.shift} {f.message}')
        print('Không lưu nghiệm mới; checkpoint cũ giữ nguyên, không mặc nhiên xác nhận hợp lệ.')
        return 2
    q=result.quality
    print_layers(q)
    if result.assignments is None:
        if result.status=='INFEASIBLE': print('Không có lịch hợp lệ với các rule hiện hành. Không tự nới rule.')
        else: print('Chưa có nghiệm; không kết luận vô nghiệm nếu UNKNOWN.')
        return 1
    print_report(result.report,result.days,q)
    print('Điểm checkpoint cũ (tính theo dữ liệu hiện tại):',result.old_vector)
    print('Điểm nghiệm lần này:',result.new_vector)
    if result.checkpoint_action=='KEPT_OLD': print('FRESH: lịch vừa tìm kém hơn checkpoint; KHÔNG ghi đè checkpoint tốt nhất.')
    elif result.checkpoint_action=='SAVED_EQUAL_PROOF': print('Đã lưu nghiệm ngang điểm và giữ chứng minh cũ còn hiệu lực:',path)
    elif result.checkpoint_action=='SAVED': print('Đã lưu checkpoint hợp lệ:',path)
    print('Số tầng liên tiếp đã chứng minh trong nghiệm lần này:',q.proven_prefix,'/ 9')
    if result.checkpoint_action=='SAVED_EQUAL_PROOF': print('Số tầng chứng minh lưu trong checkpoint:',result.candidate.proven_prefix,'/ 9')
    if result.validation.report.hard_pending:
        print('Còn giờ làm/nghỉ phi ca chưa kiểm tra; chỉ lưu nghiệm thử nghiệm.')
    print('Validator không xác nhận lịch chính thức hoặc chứng minh tối ưu.')
    if not args.no_excel and result.checkpoint_action!='KEPT_OLD':
        from datetime import datetime
        from core.reporting.excel_report import write_full_report
        export_path=args.excel or ROOT/'output'/f'AutoShift_{data.year:04d}-{data.month:02d}_4_9_{datetime.now():%Y%m%d_%H%M%S_%f}.xlsx'
        try:
            write_full_report(export_path,data,result.codes,result.validation.report,old_vector=result.old_vector)
        except (OSError,ValueError) as exc:
            print('Lịch đã xử lý; không xuất được Excel:',exc); return 3
        print('Đã xuất Excel:',export_path)
    elif result.checkpoint_action=='KEPT_OLD': print('Không xuất nghiệm FRESH kém checkpoint.')
    return 0

if __name__=='__main__': raise SystemExit(main())
