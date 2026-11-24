"""4.9: six-sheet 6.1-style roster export. Schedule is the canonical grid.
OFF remains explicit in Schedule; presentation formulas hide it only in Lich truc.
All snapshots are labeled, and existing files are not overwritten.
"""
from pathlib import Path
from datetime import datetime
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.formatting.rule import FormulaRule
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter as col
from openpyxl.worksheet.page import PageMargins
from core.data.calendar_utils import month_days, standard_credit
from core.objectives.flexible_rotation import flexible_rows
from validator.quality import score_vector

COLORS={'1':('DED9EF','473978'),'2':('D9EAF8','23577C'),'3':('FCE7CA','80501F'),'OFF':('E6EFE8','355C44'),'CT':('F5DCE3','883A50'),'DT':('D4EEF0','17666E'),'BKD':('ECEEF1','4E5965'),'T':('E5E8F6','3E4D7D')}
NAVY='244767'; LINE='E2E8F0'

def fill(color): return PatternFill('solid',fgColor=color)
def title(ws,heading,note,width):
    for r,text in [(1,heading),(2,note)]:
        ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=width)
        c=ws.cell(r,1,text); c.fill=fill(NAVY if r==1 else 'EAF0F6'); c.font=Font(name='Calibri',size=16 if r==1 else 10,bold=r==1,color='FFFFFF' if r==1 else NAVY); c.alignment=Alignment(vertical='center',wrap_text=True)
        ws.row_dimensions[r].height=29 if r==1 else 30
    ws.sheet_view.showGridLines=False

def header(ws,row,values):
    for j,v in enumerate(values,1):
        c=ws.cell(row,j,v);c.fill=fill(NAVY);c.font=Font(name='Calibri',color='FFFFFF',bold=True,size=10);c.alignment=Alignment(horizontal='center',vertical='center',wrap_text=True)
    ws.row_dimensions[row].height=29

def body(ws,row,values):
    for j,v in enumerate(values,1):
        c=ws.cell(row,j,v);c.font=Font(name='Calibri',size=10,color='263746');c.alignment=Alignment(vertical='center',horizontal='left' if j==2 else 'center',wrap_text=False);c.fill=fill('F5F8FB' if row%2 else 'FFFFFF');c.border=Border(bottom=Side(style='hair',color=LINE))
    ws.row_dimensions[row].height=19

def widths(ws,values):
    for j,w in enumerate(values,1): ws.column_dimensions[col(j)].width=w

def code_colors(ws,first,last,start,end,blank_off=False):
    rg=f'{col(start)}{first}:{col(end)}{last}'; anchor=f'{col(start)}{first}'
    for code,(bg,fg) in COLORS.items():
        if code=='OFF' and blank_off: continue
        ws.conditional_formatting.add(rg,FormulaRule(formula=[f'{anchor}&""="{code}"'],fill=fill(bg),font=Font(color=fg,bold=True)))

def write_full_report(path,data,codes,report,*,old_vector=None):
    """Export a checked snapshot, not a declaration of official approval."""
    path=Path(path)
    if path.suffix.lower()!='.xlsx': raise ValueError('Cần file .xlsx')
    if path.exists(): raise FileExistsError(path)
    if report.errors or score_vector(report) is None: raise ValueError('Không xuất lịch có lỗi cứng/thiếu điểm.')
    days=month_days(data.year,data.month); people=tuple(sorted(data.employees,key=lambda e:e.id)); N=len(people); D=len(days); end=3+D; L=col(end)
    if set(codes)!={(e.id,d) for e in people for d in days}: raise ValueError('Thiếu/thừa ô lịch.')
    wb=Workbook(); main=wb.active; main.title='Lich truc'; stat=wb.create_sheet('Thong ke'); duty=wb.create_sheet('Bo tri ca'); check=wb.create_sheet('Kiem tra'); src=wb.create_sheet('Schedule'); guide=wb.create_sheet('Huong dan')
    period=f'{data.month:02d}/{data.year}'; note='BẢN THỬ NGHIỆM • Chỉnh sửa tại Schedule • Chưa xác nhận lịch chính thức'
    header(src,1,['id','name','group',*[d.isoformat() for d in days]])
    for j,d in enumerate(days,4):
        if d in data.holidays or d.weekday()>=5: src.cell(1,j).fill=fill('94643D')
    for r,e in enumerate(people,2): body(src,r,[e.id,e.name,e.group,*[codes[e.id,d] for d in days]])
    widths(src,[7,25,10]+[13]*D);src.freeze_panes='D2';src.sheet_view.showGridLines=False
    dv=DataValidation(type='list',formula1='"1,2,3,CT,DT,BKD,T,OFF"',allow_blank=False,showErrorMessage=True,error='Chọn mã hợp lệ; ngày nghỉ phải ghi OFF.');src.add_data_validation(dv);dv.add(f'D2:{L}{N+1}');code_colors(src,2,N+1,4,end)
    title(main,f'LỊCH TRỰC THÁNG {period}',note+' | Ô nghỉ để trống',end+4)
    header(main,4,['ID','Họ và tên','Nhóm',*[d.day for d in days],'Công','Chuẩn','Thiếu','Vượt'])
    for j,d in enumerate(days,4):
        c=main.cell(5,j,('T'+str(d.weekday()+2)) if d.weekday()<6 else 'CN');c.alignment=Alignment(horizontal='center');c.font=Font(size=9,color=NAVY,bold=True)
        if d in data.holidays or d.weekday()>=5:
            main.cell(4,j).fill=fill('94643D');c.fill=fill('F1E4D4')
    for r,e in enumerate(people,6):
        sr=r-4; tr=r-1
        formulas=[f'=Schedule!{col(j)}{sr}' for j in range(1,4)]
        for j in range(4,end+1):
            ref=f'Schedule!{col(j)}{sr}'; formulas.append(f'=IF({ref}="OFF","",IF({ref}="","?",{ref}))')
        formulas.extend(f"='Thong ke'!{c}{tr}" for c in ('L','M','N','O'));body(main,r,formulas)
    widths(main,[6,25,9]+[3.5]*D+[7,7,7,7]);main.freeze_panes='D6';code_colors(main,6,N+5,4,end,True)
    row=N+8
    for j,(code,(bg,fg)) in enumerate(COLORS.items()):
        c=main.cell(row,4+j*3,'Trống = nghỉ' if code=='OFF' else code);c.fill=fill(bg);c.font=Font(bold=True,color=fg,size=9);main.merge_cells(start_row=row,start_column=4+j*3,end_row=row,end_column=6+j*3)
    main.sheet_properties.pageSetUpPr.fitToPage=True;main.page_setup.orientation='landscape';main.page_setup.paperSize=main.PAPERSIZE_A3;main.page_setup.fitToWidth=1;main.page_setup.fitToHeight=1;main.print_title_rows='1:5';main.print_area=f'A1:{col(end+4)}{row}';main.page_margins=PageMargins(left=.2,right=.2,top=.3,bottom=.3)
    title(stat,'THỐNG KÊ NHÂN SỰ','Công và số ca tự cập nhật từ Schedule. Các cột E05 là ảnh chụp lúc xuất, cần kiểm tra lại sau khi sửa.',22)
    header(stat,4,['ID','Họ và tên','Nhóm','Ca 1','Ca 2','Ca 3','CT','DT','BKD','T','OFF','Công','Chuẩn','Thiếu','Vượt','Trực lễ','Trực cuối tuần','Chuyển ca*','Nghỉ ngắn*','Dư chuỗi*','Dư 7 ngày*','Bất lợi*'])
    a={(i,d):int(v) if v in ('1','2','3') else 0 for (i,d),v in codes.items()}; flex={x.id:x for x in flexible_rows(data,days,a)}
    for r,e in enumerate(people,5):
        sr=r-3; rg=f'Schedule!$D{sr}:${L}{sr}'; vals=[f'=Schedule!{col(j)}{sr}' for j in range(1,4)]
        vals += [f'=COUNTIF({rg},"{code}")' for code in ('1','2','3','CT','DT','BKD','T','OFF')]
        vals += ['='+ '+'.join(f'{col(j)}{r}*{data.rules["work_credit"][c]}' for j,c in enumerate(('1','2','3','CT','DT','BKD','T','OFF'),4)),f"=VLOOKUP(A{r},'Huong dan'!$D$16:$F${15+N},2,FALSE)",f'=MAX(0,M{r}-L{r})',f'=MAX(0,L{r}-M{r})']
        for ds in ([d for d in days if d in data.holidays],[d for d in days if d.weekday()>=5 and d not in data.holidays]):
            vals.append('='+('+'.join(f'COUNTIF(Schedule!{col(days.index(d)+4)}{sr},"{s}")' for d in ds for s in (1,2,3)) or '0'))
        x=flex.get(e.id);vals += [x.changes,x.short,x.streak,x.concentration,x.short+x.streak+x.concentration] if x else [None]*5
        body(stat,r,vals)
    body(stat,N+5,['TỔNG',None,None,*[f'=SUM({col(j)}5:{col(j)}{N+4})' for j in range(4,23)]])
    widths(stat,[7,25,10]+[10]*14+[12]*5);stat.freeze_panes='D5'
    title(duty,'BỐ TRÍ NHÂN SỰ THEO CA','Số lượng tự cập nhật; danh sách họ tên là ảnh chụp tại thời điểm xuất. Kiểm tra lại sau chỉnh sửa.',8)
    header(duty,4,['Ngày','Thứ','Số ca 1','Số ca 2','Số ca 3','Danh sách ca 1','Danh sách ca 2','Danh sách ca 3'])
    for r,d in enumerate(days,5):
        c=col(r-1); vals=[d.isoformat(),'CN' if d.weekday()==6 else 'T'+str(d.weekday()+2)]
        vals += [f'=COUNTIF(Schedule!{c}$2:{c}${N+1},"{s}")' for s in (1,2,3)]
        vals += [', '.join(e.name+(' *' if e.is_leader else '') for e in people if codes[e.id,d]==str(s)) for s in (1,2,3)];body(duty,r,vals)
        for j in (6,7,8): duty.cell(r,j).alignment=Alignment(wrap_text=True,vertical='center')
        duty.row_dimensions[r].height=38
    widths(duty,[15,8,11,11,11,45,45,45]);duty.freeze_panes='C5'
    title(check,'BÁO CÁO VALIDATOR','Ảnh chụp lúc xuất • '+report.verdict+' • Không xác nhận lịch chính thức',5)
    body(check,4,['Lỗi cứng',len(report.errors),'Cảnh báo',len(report.warnings),'Chờ: '+', '.join(report.hard_pending)]);header(check,6,['Rule','Trạng thái','Điểm','Ghi chú','Chi tiết cảnh báo'])
    for r,x in enumerate(report.results,7):
        detail='\n'.join(f'ID={f.employee_id} {f.day or ""}: {f.message}' for f in x.findings)
        body(check,r,[x.rule_id,x.status.value,str(x.score) if x.score is not None else None,x.note,detail or None]);
        for j in (4,5): check.cell(r,j).alignment=Alignment(wrap_text=True,vertical='center')
        check.row_dimensions[r].height=min(110,max(32,15*(1+len(detail)//70),15*(1+len(x.note)//75)))
    rr=7+len(report.results)+2;header(check,rr,['So sánh E05 mới','Trước','Sau','Ý nghĩa',''])
    after=score_vector(report)[7];before=old_vector[7] if old_vector is not None and len(old_vector)==9 else (None,)*4
    for i,label in enumerate(('Nghỉ ngắn','Dư chuỗi >2','Dư loại ca >3 / 7 ngày','Max bất lợi/người')): body(check,rr+i+1,[label,before[i],after[i],'Càng thấp càng tốt',None])
    widths(check,[28,21,25,80,78]);check.freeze_panes='B7'
    title(guide,'HƯỚNG DẪN SỬ DỤNG',f'AutoShift 4.9 • Mẫu 6.1 | {period}',6);header(guide,4,['Sheet / nội dung','Cách dùng'])
    for r,(k,v) in enumerate([('Lich truc','Ô nghỉ để trống; không sửa công thức.'),('Thong ke','Thống kê tự cập nhật; cột E05 là snapshot.'),('Bo tri ca','Số lượng tự cập nhật; danh sách tên là snapshot.'),('Kiem tra','Báo cáo lúc xuất, cần chạy lại sau chỉnh lịch.'),('Schedule','Chỉ sửa mã ở đây. Ngày nghỉ ghi OFF; ô trống là lỗi.'),('Kiểm tra','python -m validator --schedule <file.xlsx>'),('Trạng thái','THỬ NGHIỆM — chưa xác nhận lịch chính thức.')],5):
        guide.cell(r,1,k);guide.merge_cells(start_row=r,start_column=2,end_row=r,end_column=6);guide.cell(r,2,v).alignment=Alignment(wrap_text=True,vertical='center');guide.row_dimensions[r].height=25
    header(guide,15,['Mã','Công','', 'ID','Công chuẩn','Phụ trách'])
    for r,(c,v) in enumerate(data.rules['work_credit'].items(),16):guide.cell(r,1,c);guide.cell(r,2,v)
    for r,e in enumerate(people,16):guide.cell(r,4,e.id);guide.cell(r,5,standard_credit(data) if e.target_credit is None else e.target_credit);guide.cell(r,6,'Có' if e.is_leader else 'Không')
    widths(guide,[24,24,10,12,15,18]);wb.active=0
    path.parent.mkdir(parents=True,exist_ok=True);buf=BytesIO();wb.save(buf);wb.close()
    with path.open('xb') as f:f.write(buf.getvalue())
    return path
