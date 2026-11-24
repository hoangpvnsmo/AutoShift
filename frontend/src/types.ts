export type Code='1'|'2'|'3'|'OFF'|'CT'|'DT'|'BKD'|'T'
export interface Employee {id:number;name:string;group:string;is_leader:boolean;allowed_shifts:number[];target_credit:number|null}
export interface Day {date:string;day:number;weekday:number;holiday:boolean}
export interface RequestRow {id:string;employee_id:number;day:string;code:Code;reason:string;status:string;revision:number}
export interface Version {id:string;month_id:number;parent_version_id:string|null;status:string;source:string;proof:Record<string,unknown>;snapshot_id:string;validation_status:string;created_at:string}
export interface PlanningMonth {id:number;year:number;month:number;annual_plan_id:number|null;current_version_id:string|null;baseline_version_id:string|null;assumptions:string;input_confirmed:boolean}
export interface CalendarData {revision:number;month:PlanningMonth;days:Day[];employees:Employee[];version:Version|null;assignments:{employee_id:number;day:string;code:Code}[];requests:RequestRow[];cancelled_requests?:RequestRow[];preferences:unknown[];locks:{employee_id:number;day:string;reason:string}[];demands:{day:string;shift:number;count:number}[];validation:any;versions:Version[];history_gaps:number;work_credit:Record<Code,number>;min_leaders_per_shift:number}
export interface Job {id:string;mode:string;status:string;stage:string;log:string;result:any;error:string|null;options:any;created_at:string;started_at:string|null;finished_at:string|null}
