"""5.5: strict, read-only Excel roster import; optional checkpoint snapshot export.

The solver is not imported. Excel is an interchange format, not a source of
employee rights, holiday definitions, requests, or objective proofs.
"""
from datetime import date, datetime, time
from io import BytesIO
from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter
from core.data.calendar_utils import month_days
from .structure import grid_result
from .result import Status

SHEET = 'Schedule'
PREFIX = ('id', 'name', 'group')


def _xlsx(path):
    path = Path(path)
    if path.suffix.lower() != '.xlsx':
        raise ValueError('Chỉ hỗ trợ file .xlsx.')
    return path


def _header_date(v):
    if type(v) is date:
        return v
    if type(v) is datetime and v.time() == time(0):
        return v.date()
    if type(v) is str:
        d = date.fromisoformat(v)
        if v == d.isoformat():
            return d
    raise ValueError(f'Ngày phải là YYYY-MM-DD hoặc ngày Excel: {v!r}')


def read_schedule(path, data):
    """Return triples, retaining duplicate IDs, missing cells and invalid codes.

    Numeric shift codes 1/2/3 are accepted as equivalent text. No other value
    is normalized. Formula cells (even if cached) are rejected explicitly.
    """
    path = _xlsx(path)
    days = month_days(data.year, data.month)
    people = {e.id: e for e in data.employees}
    width = len(PREFIX) + len(days)
    wb = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        if SHEET not in wb.sheetnames:
            raise ValueError(f'Thiếu sheet {SHEET}.')
        ws = wb[SHEET]
        # Do not rely on potentially stale dimensions written by other editors.
        ws.reset_dimensions()
        rows = iter(ws.iter_rows())
        header = next(rows, ())
        def values(row, line):
            vals = []
            for j, cell in enumerate(row, 1):
                if cell.data_type == 'f':
                    raise ValueError(f'{SHEET}!{get_column_letter(j)}{line}: không chấp nhận công thức; dán giá trị thực tế.')
                vals.append(cell.value)
            if any(v is not None for v in vals[width:]):
                raise ValueError(f'{SHEET} dòng {line}: có dữ liệu ngoài các cột ngày của tháng.')
            return (vals + [None] * width)[:width]
        h = values(header, 1)
        if tuple(h[:3]) != PREFIX:
            raise ValueError(f'{SHEET}!A1:C1: cần đúng id, name, group.')
        for j, expected in enumerate(days, 4):
            try:
                actual = _header_date(h[j-1])
            except (TypeError, ValueError) as exc:
                raise ValueError(f'{SHEET}!{get_column_letter(j)}1: {exc}') from exc
            if actual != expected:
                raise ValueError(f'{SHEET}!{get_column_letter(j)}1: cần {expected}, thực tế {actual}; sai tháng/thứ tự/trùng ngày.')
        cells = []
        for line, row in enumerate(rows, 2):
            vals = values(row, line)
            if all(v is None for v in vals):
                continue
            i, name, group = vals[:3]
            if type(i) is not int or i <= 0:
                raise ValueError(f'{SHEET}!A{line}: ID phải là số nguyên dương, không phải {i!r}.')
            e = people.get(i)
            if e is not None and (name != e.name or group != e.group):
                raise ValueError(f'{SHEET}!B{line}:C{line}: tên/nhóm của ID {i} không khớp Employees; không dùng Excel lịch để đổi thông tin nhân sự.')
            for d, v in zip(days, vals[3:]):
                if type(v) is int and v in (1,2,3):
                    v = str(v)
                cells.append((i,d,v))
        return tuple(cells)
    finally:
        wb.close()


def write_schedule(path, data, cells, *, source='codes'):
    """Write a new snapshot only, never overwrite any existing path."""
    from collections.abc import Mapping
    path = _xlsx(path)
    if path.exists():
        raise FileExistsError(f'Không ghi đè file đã tồn tại: {path}')
    rows = tuple((i,d,c) for (i,d),c in cells.items()) if isinstance(cells, Mapping) else tuple(cells)
    check = grid_result(data, rows)
    if check.status != Status.PASS:
        raise ValueError('Không xuất lịch có cấu trúc sai.')
    codes = {(i,d):c for i,d,c in rows}
    days = month_days(data.year,data.month)
    wb = Workbook()
    try:
        ws = wb.active; ws.title = SHEET
        ws.append([*PREFIX, *(d.isoformat() for d in days)])
        for e in sorted(data.employees, key=lambda x:x.id):
            ws.append([e.id,e.name,e.group,*(codes[e.id,d] for d in days)])
            # Employee names are text even when beginning with '='.
            ws.cell(ws.max_row,2).data_type = 's'
        ws.freeze_panes = 'D2'
        ws.auto_filter.ref = ws.dimensions
        ws.column_dimensions['A'].width=9
        ws.column_dimensions['B'].width=29
        ws.column_dimensions['C'].width=12
        for j,d in enumerate(days,4):
            ws.column_dimensions[get_column_letter(j)].width=13
            if d.weekday()>=5 or d in data.holidays:
                for col in ws.iter_cols(min_col=j,max_col=j,min_row=2,max_row=ws.max_row):
                    for c in col: c.fill=PatternFill('solid',fgColor='EEF2F6')
        for c in ws[1]:
            c.fill=PatternFill('solid',fgColor='244767'); c.font=Font(color='FFFFFF',bold=True)
            c.alignment=Alignment(horizontal='center')
        for row in ws.iter_rows(min_row=2,min_col=4):
            for c in row: c.alignment=Alignment(horizontal='center')
        dv=DataValidation(type='list',formula1='"1,2,3,OFF,CT,DT,BKD,T"',allow_blank=False)
        dv.showErrorMessage=True; dv.errorStyle='stop'; dv.errorTitle='Mã không hợp lệ'; dv.error='Chọn đúng một mã lịch.'
        ws.add_data_validation(dv); dv.add(f'D2:{get_column_letter(ws.max_column)}{ws.max_row}')
        guide=wb.create_sheet('Guide')
        for row in [('AutoShift 5.5 - lịch kiểm tra',''),('Tháng',f'{data.year:04d}-{data.month:02d}'),('Nguồn',source),
                    ('Lưu ý','Bản thử nghiệm; không phải lịch chính thức.'),('Schedule','Chỉ sửa mã lịch; không đổi ID, tên, nhóm hoặc ngày.'),
                    ('Mã','1, 2, 3, OFF, CT, DT, BKD, T; không để trống hoặc dùng công thức.'),
                    ('Dữ liệu chuẩn','Employees, Requests, Previous, Holidays và rules.json được đọc riêng.'),
                    ('Checkpoint','Nếu xuất từ checkpoint, mã phi ca được dựng lại theo Requests hiện tại; chưa xác minh hash/chứng minh.')]:
            guide.append(row)
        guide.column_dimensions['A'].width=24; guide.column_dimensions['B'].width=95
        for row in guide.iter_rows():
            for c in row: c.alignment=Alignment(vertical='top',wrap_text=True)
        buf=BytesIO(); wb.save(buf)
    finally:
        wb.close()
    # Exclusive create prevents accidental replacement, even if another file
    # appears between the check and the write.
    with path.open('xb') as f:
        f.write(buf.getvalue())
    return path
