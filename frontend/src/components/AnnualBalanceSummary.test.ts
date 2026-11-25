// @vitest-environment jsdom
import {describe,it,expect} from 'vitest'
import {mount} from '@vue/test-utils'
import AnnualBalanceSummary from './AnnualBalanceSummary.vue'
const report=(total:number,weekend:number,rest:number)=>({ranges:{total_shifts:{min:total,max:total+1,gap:1},weekend_shifts:{min:weekend,max:weekend+1,gap:1},full_weekends:{min:rest,max:rest+2,gap:2}},employees:[{employee_id:1,name:'Nhân sự kiểm thử',total_shifts:total,weekend_shifts:weekend,full_weekends:rest,targets:{total_shifts:{lower:205,upper:206}}}],counted_weekends:52,pending_weekends:[]})
describe('Annual balance comparison',()=>{
 it('shows whole-year counts before and after and the target without an optimality claim',()=>{
  const wrapper=mount(AnnualBalanceSummary,{props:{before:report(202,51,2),after:report(205,58,8),details:true}})
  expect(wrapper.text()).toContain('202 → 205')
  expect(wrapper.text()).toContain('51 → 58')
  expect(wrapper.text()).toContain('2 → 8')
  expect(wrapper.text()).toContain('205–206')
  expect(wrapper.text()).toContain('không đồng nghĩa đã chứng minh tối ưu toàn năm')
 })
 it('labels current counts and missing boundary data when no comparison exists',()=>{
  const after={...report(205,58,8),counted_weekends:51,pending_weekends:[{}]}
  const wrapper=mount(AnnualBalanceSummary,{props:{after}})
  expect(wrapper.text()).toContain('Lịch đang áp dụng')
  expect(wrapper.text()).toContain('1 trường hợp cuối tuần thiếu lịch ở biên')
  expect(wrapper.text()).not.toContain('Trước')
 })
})
