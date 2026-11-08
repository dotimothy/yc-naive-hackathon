/** Existing PlayLattice AI crew. Never provision replacements by name. */
export const crew = {
  coordinator: { id: 'agt_238wb3jkhddqx3rst64ef9m1jp', name: 'CEO' },
  ingress: [
    { id: 'agt_cpx8zwn39c0z3nrv0ktqg0pnke', name: 'scout' },
    { id: 'agt_s7eayedpp53x9vyr9vshc726h5', name: 'clipper' },
  ],
  egress: [
    { id: 'agt_20af4hfprvb10e4gss0ep4bp57', name: 'caption-editor' },
    { id: 'agt_tt7gs2cx9hb2gje6f154meav09', name: 'channel-manager' },
  ],
  feedback: { id: 'agt_5646nccax9eajy7ztbdesfvdjn', name: 'analyst' },
} as const;

export const crewMembers = [...crew.ingress, ...crew.egress, crew.feedback];

/** IDs are authoritative; names are diagnostic only. Fail closed on roster drift. */
export function validateCrew(agents: ReadonlyArray<{
  id: string; multiagent?: { type: string; agents?: ReadonlyArray<{ type: string; id?: string }> } | boolean | null;
}>) {
  const byId = new Map(agents.map(agent => [agent.id, agent]));
  const missing = [crew.coordinator, ...crewMembers].filter(agent => !byId.has(agent.id));
  if (missing.length) throw new Error(`Existing crew members missing: ${missing.map(a => a.name).join(', ')}`);
  const team = byId.get(crew.coordinator.id)?.multiagent;
  if (!team || typeof team !== 'object' || team.type !== 'coordinator') throw new Error('Existing CEO is not a coordinator');
  const roster = new Set(team.agents?.map(agent => agent.id));
  const absent = crewMembers.filter(agent => !roster.has(agent.id));
  if (absent.length) throw new Error(`CEO roster missing: ${absent.map(a => a.name).join(', ')}`);
}
