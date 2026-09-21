import { requiredString } from '../values.mjs';

export function implementationResult(result) {
  if (!['continue', 'review'].includes(result.goal_status)) {
    throw new Error('Implementation needs goal_status: continue or review');
  }
  return result;
}

function evidence(items, label) {
  if (!Array.isArray(items) || !items.length) throw new Error(`${label} needs observed evidence`);
  for (const item of items) {
    requiredString(item?.source, `${label} evidence source`);
    requiredString(item?.observation, `${label} evidence observation`);
  }
}

export function verification(result, findings) {
  if (!['complete', 'gap', 'unverified'].includes(result.coverage)) {
    throw new Error('Verification needs coverage: complete, gap or unverified');
  }
  if (!Array.isArray(result.criteria) || !result.criteria.length) throw new Error('Verification needs a nonempty criteria list');
  const ids = new Set();
  const criteria = result.criteria.map(item => {
    const id = requiredString(item?.id, 'Criterion id');
    if (ids.has(id)) throw new Error(`Duplicate verification criterion ${id}`);
    ids.add(id);
    requiredString(item.requirement, `Criterion ${id} requirement`);
    requiredString(item.basis, `Criterion ${id} basis`);
    if (!['satisfied', 'unmet', 'unverified'].includes(item.status)) throw new Error(`Invalid status for criterion ${id}`);
    const observations = item.evidence ?? [];
    if (!Array.isArray(observations)) throw new Error(`Criterion ${id} evidence must be a list`);
    if (item.status === 'unverified' && !observations.length) requiredString(item.explanation, `Criterion ${id} evidence gap`);
    else evidence(observations, `Criterion ${id}`);
    return { ...item, satisfied: item.status === 'satisfied', evidence: observations };
  });
  const resolutions = result.resolved_findings ?? [];
  if (!Array.isArray(resolutions)) throw new Error('resolved_findings must be a list');
  const remaining = new Set(findings.filter(finding => finding.status === 'open').map(finding => finding.id));
  for (const resolution of resolutions) {
    if (!remaining.delete(resolution?.id)) throw new Error(`Unknown or duplicate open finding ${resolution?.id}`);
    requiredString(resolution.reason, `Finding ${resolution.id} resolution`);
    evidence(resolution.evidence, `Finding ${resolution.id} resolution`);
  }
  return { final: result.final, criteria, coverage: result.coverage, resolved_findings: resolutions,
    verified: result.coverage === 'complete' && criteria.every(item => item.satisfied) && remaining.size === 0 && result.verified !== false };
}

// Keep observations open until a verifier explicitly resolves them. The verifier
// assesses scope and evidence; the controller prevents gaps disappearing by omission.
export function reconcileFindings(findings, result, attempt) {
  for (const resolution of result.resolved_findings) {
    const finding = findings.find(item => item.id === resolution.id);
    Object.assign(finding, { status: 'resolved', resolution: resolution.reason,
      resolutionEvidence: resolution.evidence, resolvedAttempt: attempt });
  }
  const observations = result.criteria.filter(item => !item.satisfied).map(criterion => ({
    criterion_id: criterion.id, requirement: criterion.requirement, basis: criterion.basis,
    assessment: criterion.status, evidence: criterion.evidence, explanation: criterion.explanation ?? '',
  }));
  if (result.coverage !== 'complete') observations.push({ criterion_id: null,
    requirement: 'Establish verification coverage of the whole original goal', basis: 'Original goal',
    assessment: 'unverified', evidence: [], explanation: result.final });
  for (const observation of observations) {
    if (findings.some(item => item.status === 'open' && item.requirement === observation.requirement &&
        item.assessment === observation.assessment && JSON.stringify(item.evidence) === JSON.stringify(observation.evidence) &&
        item.explanation === observation.explanation)) continue;
    findings.push({ id: `F${findings.length + 1}`, ...observation, status: 'open', attempt });
  }
}
