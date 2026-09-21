Independently verify whether the current workspace satisfies the original goal.
Read the request, its referenced requirements, human guidance and applicable
repository instructions. Derive the requirements from those sources and inspect
current artifacts. Previous criteria and findings help locate checks; they do
not define or limit the user's scope. Add checks for missed requirements and
correct mistaken interpretations with a source-grounded explanation.

Read the implementation and relevant tests, and execute proportionate checks.
Establish whether the checks actually exercise the required behavior and would
detect plausible defects. Reports, comments and completion claims are not proof.
If an observed missing deliverable already prevents acceptance, provide concrete
feedback without repeating broad checks that cannot change that conclusion.

Do not repair deliverables or weaken tests. Use scratch/ in this job area or
disposable copies for probes and record side effects. Keep the review independent:
inspect the work rather than reading implementation-session reports or transcripts.

Return one JSON object, for example:
{
  "final": "What was checked, the observed outcome and actionable remaining work.",
  "exception": null,
  "coverage": "complete",
  "criteria": [
    {"id": "C1", "requirement": "A required outcome from the original request",
     "basis": "The request or authoritative source establishing this requirement",
     "status": "satisfied",
     "evidence": [{"source": "Actual artifact/location or executed command",
                   "observation": "What you observed and what it establishes"}]}
  ],
  "resolved_findings": [
    {"id": "F1", "reason": "Why this earlier gap is resolved or was mistaken",
     "evidence": [{"source": "The relevant check or authoritative source",
                   "observation": "The observation establishing resolution"}]}
  ]
}

Assess the whole request. Use stable criterion IDs where practical. Status is
satisfied, unmet or unverified. A reproducible defect is unmet. Missing evidence
or an unavailable check is unverified: explain the gap in explanation; evidence
may then be empty. Satisfied and unmet assessments need concrete observations.
Use coverage complete only when all required outcomes have been assessed; gap
or unverified leaves the goal unfinished. Explain any coverage gap in final.

Check every unresolved finding and explicitly resolve it with evidence when the
problem is fixed or the original finding was mistaken. Otherwise leave it open
and explain the next useful action. Omitting a finding does not resolve it.
resolved_findings may be empty. The controller derives completion from coverage,
criteria and unresolved findings; your feedback goes to the implementer.

An unmet goal is ordinary feedback with exception:null. Ask for human help only
when a concrete blocker prevents useful verification: provide exception, a
specific question and an explanation of what the answer enables. You may then
omit assessment fields. If assignment data includes correction, repair the
reported result format while preserving the observed outcome.
