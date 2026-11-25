<script setup lang="ts">
export interface ClearYearPreview {
 year:number;plan_id:number;revision:number;months:number;current_months:number;baseline_months:number;
 versions_to_archive:number;jobs_to_archive:number;locks_to_clear:number;requests_kept:number;
 requests_to_approve:number;can_clear:boolean;empty:boolean;blocked_reasons:string[];
}
defineProps<{modelValue:boolean;preview?:ClearYearPreview;loading:boolean}>()
const emit=defineEmits<{ 'update:modelValue':[value:boolean];confirm:[] }>()
</script>
<template>
 <el-dialog :model-value="modelValue" :title="`Clear năm ${preview?.year??''}`" width="min(580px, 94vw)" :close-on-click-modal="!loading" :close-on-press-escape="!loading" :show-close="!loading" @update:model-value="emit('update:modelValue',$event)">
  <template v-if="preview">
   <p>Đưa lịch năm <strong>{{preview.year}}</strong> về trạng thái chưa xếp để lập lại từ đầu. Thao tác áp dụng cho tất cả tháng đang có trong năm này.</p>
   <ul>
    <li>{{preview.current_months}} tháng có lịch áp dụng; {{preview.versions_to_archive}} phiên bản được đưa vào lịch sử lưu trữ.</li>
    <li>{{preview.jobs_to_archive}} kết quả Solver cũ được lưu trữ; không thể duyệt lại.</li>
    <li>{{preview.locks_to_clear}} khóa ô của lịch cũ được bỏ để xếp lại từ đầu.</li>
    <li>Giữ {{preview.requests_kept}} yêu cầu; {{preview.requests_to_approve}} yêu cầu đã áp dụng được đưa về đã duyệt để dùng khi chạy lại.</li>
   </ul>
   <p>Giữ nguyên nhân sự, quyền ca, công chuẩn, quy tắc, ngày lễ, định biên, CT/DT/BKD/OFF và nguyện vọng. Lịch các năm khác được giữ nguyên.</p>
   <p>Hệ thống tự tạo bản sao lưu trước khi Clear. Lịch cũ vẫn xem được trong lịch sử. Sau đó bấm <strong>Lập lịch năm</strong> để xếp lại.</p>
   <el-alert v-for="reason in preview.blocked_reasons" :key="reason" :title="reason" type="error" :closable="false" show-icon/>
   <el-alert v-if="preview.empty" title="Năm này đã trống, có thể lập lịch năm ngay." type="info" :closable="false" show-icon/>
  </template>
  <template #footer>
   <el-button :disabled="loading" @click="emit('update:modelValue',false)">Đóng</el-button>
   <el-button type="danger" :loading="loading" :disabled="!preview?.can_clear||loading" @click="emit('confirm')">Clear năm {{preview?.year}}</el-button>
  </template>
 </el-dialog>
</template>
