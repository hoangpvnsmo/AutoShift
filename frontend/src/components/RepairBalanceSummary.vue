<script setup lang="ts">
import {computed} from 'vue'
const props=defineProps<{report:any}>()
const status=computed(()=>({IMPROVED:'Đã cải thiện cân bằng',UNCHANGED:'Giữ phương án Repair gốc',NO_CHANGE_NEEDED:'Không cần sửa lịch',NOT_RUN_BUDGET_EXHAUSTED:'Hết thời gian trước bước cân bằng',NOT_RUN_UNSUPPORTED:'Giữ Repair gốc vì điều kiện cân chưa hỗ trợ',CANCELLED:'Đã hủy'} as Record<string,string>)[props.report.status]??props.report.status)
const rows=computed(()=>{
 const r=props.report,out:any[]=[]
 for(const group of r.after??[]){
  if(!group.month||group.field!=='shifts')continue
  const original=r.original?.find((g:any)=>g.label===group.label),stable=r.before?.find((g:any)=>g.label===group.label)
  const night=r.after.find((g:any)=>g.year===group.year&&g.month===group.month&&g.field==='night')
  const oldNight=r.original?.find((g:any)=>g.year===group.year&&g.month===group.month&&g.field==='night')
  const baseNight=r.before?.find((g:any)=>g.year===group.year&&g.month===group.month&&g.field==='night')
  const year=r.after.find((g:any)=>g.year===group.year&&!g.month&&g.field==='shifts')
  const oldYear=r.original?.find((g:any)=>g.year===group.year&&!g.month&&g.field==='shifts')
  for(const [id,count] of Object.entries(group.counts)){
   if(count===stable?.counts[id]&&night?.counts[id]===baseNight?.counts[id])continue
   out.push({month:`${group.month}/${group.year}`,id,name:r.people?.find((p:any)=>p.id===Number(id))?.name??id,
    original:original?.counts[id],stable:stable?.counts[id],selected:count,oldNight:oldNight?.counts[id]??0,night:night?.counts[id]??0,
    yearBefore:oldYear?.counts[id],yearAfter:year?.counts[id],observed:year?.observed_months})
  }
 }
 return out
})
</script>
<template>
 <section class="repair-balance-summary">
  <h3>Repair có cân bằng · {{status}}</h3>
  <p>Repair gốc {{report.base_changes}} ô → sau cân {{report.selected_changes}} ô; giới hạn {{report.change_limit}} ô, thêm {{report.extra_changes}} ô.</p>
  <p>Nghỉ ngắn mới so với lịch cũ: {{report.base_short_rest}} → {{report.selected_short_rest}}. Giữ giới hạn nguồn lực, khóa và giờ nghỉ bắt buộc.</p>
  <p v-if="report.extra_changes>0">Đã dùng thêm ô đổi để cân bằng. Phương án này không được gắn chứng minh ít ô đổi nhất.</p>
  <p v-if="report.status==='UNCHANGED'">Chưa tìm được phương án cân bằng tốt hơn trong giới hạn thời gian, số ô đổi và nghỉ ngắn.</p>
  <p v-if="report.reason">{{report.reason}}</p>
  <p>Xét tổng ca các tháng đã có lịch trong năm, rồi tổng ca tháng và ca đêm. Tháng chưa có lịch không được tính là nghỉ.</p>
  <div v-if="rows.length" class="table-scroll"><table class="data-table"><thead><tr><th>Tháng</th><th>Nhân sự</th><th>Ca lịch cũ</th><th>Ca Repair gốc</th><th>Ca sau cân</th><th>Đêm cũ</th><th>Đêm sau cân</th><th>Ca năm cũ → sau cân</th></tr></thead><tbody><tr v-for="r in rows" :key="r.month+'-'+r.id"><td>{{r.month}}</td><td>{{r.name}}</td><td>{{r.original}}</td><td>{{r.stable}}</td><td>{{r.selected}}</td><td>{{r.oldNight}}</td><td>{{r.night}}</td><td><template v-if="r.observed">{{r.yearBefore}} → {{r.yearAfter}} ({{r.observed}} tháng có lịch)</template><template v-else>Chưa có dữ liệu</template></td></tr></tbody></table></div>
 </section>
</template>
