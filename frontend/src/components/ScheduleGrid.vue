<script setup lang="ts">
import {computed} from 'vue'
import type {CalendarData,Employee,Day,Code} from '../types'
const props=defineProps<{data:CalendarData;overlay:boolean;group:string;search:string;edits:Record<string,Code>;changed?:Set<string>;affectedOnly?:boolean;reference?:Record<string,Code>}>()
const emit=defineEmits<{cell:[employee:Employee,day:Day]}>()
const key=(i:number,d:string)=>`${i}|${d}`
const cells=computed(()=>Object.fromEntries(props.data.assignments.map(a=>[key(a.employee_id,a.day),a.code])))
const requests=computed(()=>Object.fromEntries(props.data.requests.filter(r=>r.status!=='APPLIED').map(r=>[key(r.employee_id,r.day),r])))
const locks=computed(()=>new Set(props.data.locks.map(l=>key(l.employee_id,l.day))))
const people=computed(()=>props.data.employees.filter(e=>(!props.group||e.group===props.group)&&(!props.search||`${e.id} ${e.name}`.toLocaleLowerCase('vi').includes(props.search.toLocaleLowerCase('vi')))&&(!props.affectedOnly||props.data.days.some(d=>props.changed?.has(key(e.id,d.date))))).sort((a,b)=>a.group===b.group?a.id-b.id:a.group==='SHIFT'?-1:1))
const days=computed(()=>props.data.days.filter(d=>!props.affectedOnly||props.data.employees.some(e=>props.changed?.has(key(e.id,d.date)))))
const code=(i:number,d:string)=>props.edits[key(i,d)]??cells.value[key(i,d)]
const total=(i:number)=>props.data.days.reduce((n,d)=>n+(props.data.work_credit[code(i,d.date)]??0),0)
const count=(d:string,s:number)=>props.data.employees.filter(e=>code(e.id,d)===String(s)).length
const leaders=(d:string,s:number)=>props.data.employees.filter(e=>e.is_leader&&code(e.id,d)===String(s)).length
const demand=(d:string,s:number)=>props.data.demands.find(v=>v.day===d&&v.shift===s)?.count??0
</script>
<template>
<div class="schedule-scroll"><table class="schedule-table" aria-label="Lịch trực toàn phòng">
 <thead><tr><th class="id-col" rowspan="2">ID</th><th class="name-col" rowspan="2">Nhân sự <small>★ Phụ trách</small></th><th v-for="d in days" :key="d.date" :class="{weekend:d.weekday>=5,holiday:d.holiday}">{{d.day}}</th><th rowspan="2">Công</th><th rowspan="2">Ca 1</th><th rowspan="2">Ca 2</th><th rowspan="2">Ca 3</th></tr>
 <tr><th v-for="d in days" :key="d.date" :class="{weekend:d.weekday>=5,holiday:d.holiday}">{{d.weekday===6?'CN':'T'+(d.weekday+2)}}</th></tr></thead>
 <tbody><template v-for="(e,index) in people" :key="e.id">
 <tr v-if="index===0||e.group!==people[index-1]?.group" class="group-row"><td :colspan="days.length+6">{{e.group==='SHIFT'?'NHÂN SỰ ĐI CA':'HÀNH CHÍNH'}} · {{people.filter(p=>p.group===e.group).length}} người</td></tr>
 <tr><th class="id-col">{{e.id}}</th><th class="name-col"><span v-if="e.is_leader" class="leader">★ </span>{{e.name}}</th>
 <td v-for="d in days" :key="d.date" :data-employee="e.id" :data-day="d.date" :class="['code-cell','code-'+(code(e.id,d.date)??'UNSET'),{weekend:d.weekday>=5,pending:overlay&&requests[key(e.id,d.date)],changed:changed?.has(key(e.id,d.date)),edited:edits[key(e.id,d.date)]}]" :title="`${e.name} · ${d.date} · ${code(e.id,d.date)??'Chưa xếp lịch'}${requests[key(e.id,d.date)]?' · Yêu cầu '+requests[key(e.id,d.date)].code+' '+requests[key(e.id,d.date)].status:''}`" tabindex="0" @click="emit('cell',e,d)" @keydown.enter="emit('cell',e,d)">
 <span class="original-code">{{code(e.id,d.date)==='OFF'?'':code(e.id,d.date)??'·'}}</span>
 <small v-if="reference&&reference[key(e.id,d.date)]!==code(e.id,d.date)" class="previous-code">cũ {{reference[key(e.id,d.date)]==='OFF'?'OFF':reference[key(e.id,d.date)]}}</small>
 <span v-if="overlay&&requests[key(e.id,d.date)]" class="request-overlay">{{requests[key(e.id,d.date)].code}}<sup>⋆</sup></span><span v-if="locks.has(key(e.id,d.date))" class="lock-icon">▣</span>
 </td><td class="total">{{total(e.id)}}</td><td v-for="s in [1,2,3]" :key="s">{{data.days.filter(d=>code(e.id,d.date)===String(s)).length}}</td></tr>
 </template></tbody>
 <tfoot><tr v-for="s in [1,2,3]" :key="s"><th colspan="2" class="footer-label">Ca {{s}} · số người / nhu cầu</th><td v-for="d in days" :key="d.date" :class="{shortage:count(d.date,s)!==demand(d.date,s)||leaders(d.date,s)<data.min_leaders_per_shift}" :title="`Phụ trách: ${leaders(d.date,s)} / ${data.min_leaders_per_shift}`">{{count(d.date,s)}}<small>/{{demand(d.date,s)}}</small></td><td colspan="4"></td></tr></tfoot>
</table><p v-if="!people.length" class="empty">Không có nhân sự phù hợp bộ lọc.</p></div>
</template>
