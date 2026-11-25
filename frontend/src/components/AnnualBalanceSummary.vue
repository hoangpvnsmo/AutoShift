<script setup lang="ts">
import {computed} from 'vue'
const props=defineProps<{before?:any;after:any;details?:boolean}>()
const labels:Record<string,string>={total_shifts:'Tổng ca 1/2/3 trong năm',weekend_shifts:'Ca trực thứ Bảy và Chủ nhật',full_weekends:'Cuối tuần nghỉ trọn theo giờ thực'}
const previous=computed(()=>Object.fromEntries((props.before?.employees??[]).map((e:any)=>[e.employee_id,e])))
const range=(report:any,key:string)=>{const r=report?.ranges?.[key];return r?`${r.min}–${r.max} (chênh ${r.gap})`:'—'}
const target=(e:any)=>{const t=e.targets?.total_shifts;return t?`${t.lower}–${t.upper}`:'—'}
</script>
<template>
 <section v-if="after" class="repair-summary">
  <strong>Cân bằng lũy kế năm · nhóm trực</strong>
  <table class="data-table"><thead><tr><th>Chỉ tiêu</th><th v-if="before">Trước</th><th>{{before?'Sau':'Lịch đang áp dụng'}}</th></tr></thead><tbody><tr v-for="(label,key) in labels" :key="key"><th>{{label}}</th><td v-if="before">{{range(before,key)}}</td><td>{{range(after,key)}}</td></tr></tbody></table>
  <p>Đếm {{after.counted_weekends}} cuối tuần có đủ dữ liệu. Tổng ca chỉ tính 1/2/3; CT/DT/T theo dõi riêng. Ca T7/CN tính cả ngày trùng lễ. Nghỉ trọn kiểm tra cả ca đêm bắt đầu tối Chủ nhật.</p>
  <p v-if="after.pending_weekends?.length" class="notice">Còn {{after.pending_weekends.length}} trường hợp cuối tuần thiếu lịch ở biên; chưa được tính là đã nghỉ.</p>
  <div v-if="details" class="table-scroll"><table class="data-table"><thead><tr><th>Nhân sự</th><th>Mục tiêu tổng ca</th><th>Tổng ca {{before?'cũ → mới':''}}</th><th>Ca cuối tuần {{before?'cũ → mới':''}}</th><th>Nghỉ trọn {{before?'cũ → mới':''}}</th></tr></thead><tbody><tr v-for="e in after.employees" :key="e.employee_id"><td>{{e.name}}</td><td>{{target(e)}}</td><td><template v-if="before">{{previous[e.employee_id]?.total_shifts??'—'}} → </template>{{e.total_shifts}}</td><td><template v-if="before">{{previous[e.employee_id]?.weekend_shifts??'—'}} → </template>{{e.weekend_shifts}}</td><td><template v-if="before">{{previous[e.employee_id]?.full_weekends??'—'}} → </template>{{e.full_weekends}}</td></tr></tbody></table></div>
  <p v-if="before">Đây là phương án đã kiểm tra; mức cân bằng đạt được không đồng nghĩa đã chứng minh tối ưu toàn năm hoặc số ô đổi nhỏ nhất.</p>
 </section>
</template>
