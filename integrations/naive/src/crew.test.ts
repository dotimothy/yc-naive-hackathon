import { test } from 'node:test';
import assert from 'node:assert/strict';
import { crew, crewMembers, validateCrew } from './crew.js';
const valid = () => [
  { id: crew.coordinator.id, multiagent: { type: 'coordinator', agents: crewMembers.map(a => ({ type: 'agent', id: a.id })) } },
  ...crewMembers.map(a => ({ id: a.id, multiagent: false })),
];
test('binds existing coordinator and specialists', () => assert.doesNotThrow(() => validateCrew(valid())));
test('missing member fails closed', () => assert.throws(() => validateCrew(valid().slice(0, -1)), /missing/));
test('roster drift fails closed', () => {
  const agents = valid();
  agents[0].multiagent = { type: 'coordinator', agents: [] };
  assert.throws(() => validateCrew(agents), /roster missing/);
});
test('boolean multiagent is not a coordinator', () => {
  const agents = valid(); agents[0].multiagent = false;
  assert.throws(() => validateCrew(agents), /not a coordinator/);
});
