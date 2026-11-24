import {defineStore} from 'pinia'
import {ref} from 'vue'
import type {CalendarData,PlanningMonth,Job} from './types'
export async function api<T=any>(url:string,options:RequestInit={}):Promise<T>{
 const r=await fetch('/api'+url,{...options,headers:{'Content-Type':'application/json',...options.headers}})
 if(!r.ok){const data=await r.json().catch(()=>({detail:r.statusText}));throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail))}
 return r.json()
}
export const post=<T=any>(url:string,data:unknown)=>api<T>(url,{method:'POST',body:JSON.stringify(data)})
export const put=<T=any>(url:string,data:unknown)=>api<T>(url,{method:'PUT',body:JSON.stringify(data)})
export const usePlanning=defineStore('planning',()=>{
 const months=ref<PlanningMonth[]>([]),monthId=ref<number>(),calendar=ref<CalendarData>(),jobs=ref<Job[]>([]),revision=ref(1),error=ref(''),busy=ref(false)
 async function refresh(versionId?:string){
  busy.value=true
  try{
   const [state,all,j]=await Promise.all([api('/health'),api<PlanningMonth[]>('/months'),api<Job[]>('/jobs')])
   revision.value=state.revision;months.value=all;jobs.value=j
   if(!monthId.value&&all.length)monthId.value=all[0].id
   if(monthId.value)calendar.value=await api<CalendarData>(`/months/${monthId.value}/calendar${versionId?'?version_id='+versionId:''}`)
  }catch(e){error.value=String(e)}finally{busy.value=false}
 }
 return {months,monthId,calendar,jobs,revision,error,busy,refresh}
})
