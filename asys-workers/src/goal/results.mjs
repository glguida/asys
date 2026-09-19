import { requiredString } from '../values.mjs';

export function contract(result) {
  const proposal = result.contract;
  if (!proposal || !Array.isArray(proposal.criteria) || !proposal.criteria.length) {
    throw new Error('Definition needs a contract with a nonempty criteria list');
  }
  const ids = new Set();
  for (const criterion of proposal.criteria) {
    const id = requiredString(criterion?.id, 'Criterion id');
    if (ids.has(id)) throw new Error(`Duplicate criterion ${id}`);
    ids.add(id);
    for (const field of ['requirement', 'basis', 'verification']) requiredString(criterion[field], `Criterion ${id} ${field}`);
  }
  return proposal;
}

export function review(result) {
  if (!['accept', 'revise'].includes(result.decision)) throw new Error('Contract review needs decision: accept or revise');
  return result;
}

function evidence(items, label) {
  if (!Array.isArray(items) || !items.length) throw new Error(`${label} needs observed evidence`);
  for (const item of items) {
    requiredString(item?.source, `${label} evidence source`);
    requiredString(item?.observation, `${label} evidence observation`);
  }
}

export function verification(result, accepted, findings) {
  if (!['complete', 'gap', 'unverified'].includes(result.coverage)) {
    throw new Error('Verification needs coverage: complete, gap or unverified');
  }
  if (!Array.isArray(result.criteria)) throw new Error('Verification needs a criteria list');
  const expected = new Map(accepted.criteria.map(criterion => [criterion.id, criterion]));
  const criteria = result.criteria.map(item => {
    const criterion = expected.get(item?.id);
    if (!criterion) throw new Error(`Unknown or duplicate verification criterion ${item?.id}`);
    expected.delete(item.id);
    if (!['satisfied', 'unmet', 'unverified'].includes(item.status)) throw new Error(`Invalid status for criterion ${item.id}`);
    const observations = item.evidence ?? [];
    if (!Array.isArray(observations)) throw new Error(`Criterion ${item.id} evidence must be a list`);
    if (item.status === 'unverified' && !observations.length) requiredString(item.explanation, `Criterion ${item.id} evidence gap`);
    else evidence(observations, `Criterion ${item.id}`);
    return { ...item, requirement: criterion.requirement, satisfied: item.status === 'satisfied', evidence: observations };
  });
  if (expected.size) throw new Error(`Verification omitted criteria: ${[...expected.keys()].join(', ')}`);
  const resolutions = result.resolved_findings ?? [];
  if (!Array.isArray(resolutions)) throw new Error('resolved_findings must be a list');
  const remaining = new Set(findings.filter(finding => finding.status === 'open').map(finding => finding.id));
  for (const resolution of resolutions) {
    if (!remaining.delete(resolution?.id)) throw new Error(`Unknown or duplicate open finding ${resolution?.id}`);
    requiredString(resolution.reason, `Finding ${resolution.id} resolution`);
  }
  if (result.coverage === 'gap') requiredString(result.contract_changes, 'Describe the contract gap in contract_changes');
  return { final: result.final, criteria, coverage: result.coverage,
    contract_changes: result.contract_changes ?? null, resolved_findings: resolutions,
    verified: result.coverage === 'complete' && !result.contract_changes && criteria.every(item => item.satisfied) &&
      remaining.size === 0 && result.verified !== false };
}

// Preserve each distinct observed gap until a verifier explicitly resolves it.
// Criteria and evidence are assessed by the agents; these checks only track work.
export function reconcileFindings(findings, result, attempt) {
  for (const resolution of result.resolved_findings) {
    const finding = findings.find(item => item.id === resolution.id);
    Object.assign(finding, { status: 'resolved', resolution: resolution.reason, resolvedAttempt: attempt });
  }
  for (const criterion of result.criteria.filter(item => !item.satisfied)) {
    const observation = { criterion_id: criterion.id, requirement: criterion.requirement, assessment: criterion.status,
      evidence: criterion.evidence, explanation: criterion.explanation ?? '' };
    if (findings.some(item => item.status === 'open' && item.criterion_id === observation.criterion_id && item.assessment === observation.assessment &&
      JSON.stringify(item.evidence) === JSON.stringify(observation.evidence) && item.explanation === observation.explanation)) continue;
    findings.push({ id: `F${findings.length + 1}`, ...observation, status: 'open', attempt });
  }
}
