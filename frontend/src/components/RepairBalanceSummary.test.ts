// @vitest-environment jsdom
import {describe,it,expect} from 'vitest'
import {mount} from '@vue/test-utils'
import RepairBalanceSummary from './RepairBalanceSummary.vue'
const group=(field:string,counts:any)=>({label:field,year:2027,month:12,field,counts})
const report={status:'IMPROVED',base_changes:8,selected_changes:10,change_limit:10,extra_changes:2,base_short_rest:1,selected_short_rest:1,
 people:[{id:13,name:'Phạm Việt Hoàng'}],original:[group('shifts',{13:19}),group('night',{13:6})],
 before:[group('shifts',{13:20}),group('night',{13:7})],after:[group('shifts',{13:19}),group('night',{13:6})]}
describe('Bounded Repair result',()=>{
 it('shows baseline, extra edits and real shift/night comparison without a false optimality claim',()=>{
  const w=mount(RepairBalanceSummary,{props:{report}})
  expect(w.text()).toContain('8 ô → sau cân 10 ô')
  expect(w.text()).toContain('Nghỉ ngắn mới so với lịch cũ: 1 → 1')
  expect(w.text()).toContain('không được gắn chứng minh ít ô đổi nhất')
  expect(w.text()).toContain('Phạm Việt Hoàng');expect(w.findAll('tbody tr')).toHaveLength(1)
 })
 it('explains a fallback rather than pretending fairness improved',()=>{
  const w=mount(RepairBalanceSummary,{props:{report:{...report,status:'UNCHANGED',selected_changes:8,extra_changes:0,after:report.before}}})
  expect(w.text()).toContain('Giữ phương án Repair gốc')
  expect(w.text()).toContain('Chưa tìm được phương án cân bằng tốt hơn')
  expect(w.findAll('tbody tr')).toHaveLength(0)
 })
})
