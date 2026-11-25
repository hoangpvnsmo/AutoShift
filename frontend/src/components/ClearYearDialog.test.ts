// @vitest-environment jsdom
import {describe,it,expect} from 'vitest'
import {mount} from '@vue/test-utils'
import {ElButton,ElAlert} from 'element-plus'
import ClearYearDialog from './ClearYearDialog.vue'
const preview={year:2027,plan_id:2,revision:38,months:12,current_months:12,baseline_months:12,
 versions_to_archive:24,jobs_to_archive:2,locks_to_clear:3,requests_kept:9,requests_to_approve:8,
 can_clear:true,empty:false,blocked_reasons:[] as string[]}
const render=(overrides={},loading=false)=>mount(ClearYearDialog,{props:{modelValue:true,preview:{...preview,...overrides},loading},
 global:{components:{ElButton,ElAlert},stubs:{ElDialog:{template:'<div><slot/><slot name="footer"/></div>'}}}})
describe('Clear year confirmation',()=>{
 it('shows the chosen year, lock removal and preserved inputs before confirming',async()=>{
  const w=render()
  expect(w.text()).toContain('3 khóa ô')
  expect(w.text()).toContain('Giữ 9 yêu cầu')
  expect(w.text()).toContain('Lịch các năm khác được giữ nguyên')
  expect(w.text()).toContain('tự tạo bản sao lưu')
  expect(w.emitted('confirm')).toBeUndefined()
  await w.findAll('button').at(-1)!.trigger('click')
  expect(w.emitted('confirm')).toHaveLength(1)
 })
 it('blocks confirmation when a solver is active or the year is already empty',async()=>{
  for(const overrides of [{can_clear:false,blocked_reasons:['Đang có Solver chạy']},{can_clear:false,empty:true}]){
   const w=render(overrides),button=w.findAll('button').at(-1)!
   expect(button.attributes('disabled')).toBeDefined()
   await button.trigger('click');expect(w.emitted('confirm')).toBeUndefined()
  }
 })
 it('prevents a second confirmation during clear',async()=>{
  const w=render({},true)
  expect(w.findAll('button').every(button=>button.attributes('disabled')!==undefined)).toBe(true)
  await w.findAll('button').at(-1)!.trigger('click');expect(w.emitted('confirm')).toBeUndefined()
 })
})
