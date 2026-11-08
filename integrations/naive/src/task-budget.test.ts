import {test} from 'node:test';
import assert from 'node:assert/strict';
import {WorkflowStore} from './analysis-store.js';
import {reserveTask,settleTask,taskBudgetTotal} from './task-budget.js';
test('standalone discovery reservations share the monthly allocation and settle to actual cost',()=>{
 const s=new WorkflowStore(':memory:');try{
  const month=new Date().toISOString().slice(0,7);s.save({job_id:'existing',game_id:'g',state:'succeeded',month,reserved:7900000});
  reserveTask(s.db,'scout','Discovery',150000);reserveTask(s.db,'scout','Discovery',150000);assert.equal(taskBudgetTotal(s.db),150000);
  assert.throws(()=>s.reserve({job_id:'next',game_id:'n',state:'submitting',month}),/monthly/);
  assert.throws(()=>settleTask(s.db,'scout',150001,'session'),/cap/);assert.equal(taskBudgetTotal(s.db),150000);
  settleTask(s.db,'scout',57860,'session');assert.equal(taskBudgetTotal(s.db),57860);assert.throws(()=>settleTask(s.db,'scout',57861,'session'),/cap/);
  assert.equal(s.reserve({job_id:'next',game_id:'n',state:'submitting',month}),true);
 }finally{s.close();}
});
