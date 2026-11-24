// @vitest-environment jsdom
import {describe,it,expect} from 'vitest'
import {mount} from '@vue/test-utils'
import ScheduleGrid from './ScheduleGrid.vue'
import type {CalendarData} from '../types'
function sample(code?:string):CalendarData{return {revision:1,month:{id:1,year:2026,month:12,annual_plan_id:null,current_version_id:'base',baseline_version_id:'base',assumptions:'Test',input_confirmed:true},days:[{date:'2026-12-15',day:15,weekday:1,holiday:false}],employees:[{id:7,name:'Nhân sự kiểm thử',group:'SHIFT',is_leader:true,allowed_shifts:[1,2,3],target_credit:null}],version:null,assignments:code?[{employee_id:7,day:'2026-12-15',code:code as any}]:[],requests:[{id:'r1',employee_id:7,day:'2026-12-15',code:'DT',reason:'Đào tạo',status:'APPROVED',revision:1}],preferences:[],locks:[],demands:[{day:'2026-12-15',shift:2,count:1}],validation:null,versions:[],history_gaps:0,work_credit:{'1':1,'2':1,'3':1,OFF:0,CT:1,DT:1,BKD:0,T:1},min_leaders_per_shift:1}}
const props=(data:CalendarData)=>({data,overlay:true,group:'',search:'',edits:{}})
describe('Request overlay is separate from the schedule',()=>{
 it('keeps the published shift and displays the pending DT request',async()=>{
  const wrapper=mount(ScheduleGrid,{props:props(sample('2'))})
  const cell=wrapper.get('[data-employee="7"]')
  expect(cell.get('.original-code').text()).toBe('2')
  expect(cell.get('.request-overlay').text()).toContain('DT')
  await wrapper.setProps({overlay:false})
  expect(cell.find('.request-overlay').exists()).toBe(false)
  expect(cell.get('.original-code').text()).toBe('2')
 })
 it('distinguishes explicit OFF from an unscheduled cell',()=>{
  const off=mount(ScheduleGrid,{props:props(sample('OFF'))})
  const unset=mount(ScheduleGrid,{props:props(sample())})
  expect(off.get('.original-code').text()).toBe('')
  expect(unset.get('.original-code').text()).toBe('·')
  expect(unset.get('.request-overlay').text()).toContain('DT')
 })
 it('opens the cell with the original employee and date',async()=>{
  const data=sample('2'),wrapper=mount(ScheduleGrid,{props:props(data)})
  await wrapper.get('[data-employee="7"]').trigger('click')
  expect(wrapper.emitted('cell')?.[0]).toEqual([data.employees[0],data.days[0]])
 })
})
