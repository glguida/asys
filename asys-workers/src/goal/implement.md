Work toward the original goal in this workspace, following its referenced
requirements and applicable repository instructions. This is a continuing
implementation conversation. Plan and perform the work as you would for a normal
assignment; keep the full requested outcome intact across turns. Work through
meaningful tasks rather than deliberately ending after tiny increments.

Use current artifacts and executed checks to establish progress. Previous
conversation and verifier findings help you locate work, but recheck claims
against the current workspace. Investigate the supplied feedback, fix remaining
problems and run proportionate checks. Explain a mistaken finding with evidence.
Do not weaken requirements or tests to obtain approval.

Ending a turn does not finish the goal. When useful work remains, return
goal_status:"continue"; the next turn automatically continues this conversation.
Make progress through work, evidence that changes the next action, or observation
of a process confirmed live now. Status restatements and plans alone are not
progress. Recheck current state and take the next available useful action.

Before claiming readiness, audit the whole original request against current
artifacts and executed checks. Account for every requested outcome and any open
findings. Check that the evidence actually covers the requirement; a narrow
passing test does not establish a broader result. When the full goal appears
satisfied, return goal_status:"review". A fresh independent verifier then checks
the work. Any unresolved findings return to this conversation for further work.
Keep the original scope even near a configured attempt limit. Git actions follow
the user's request and repository policy.

Return one JSON object with final, exception:null, and goal_status set to
"continue" or "review". In final, give a factual account of work, checks and
remaining gaps. Ask for human help only when a concrete blocker prevents useful
progress: put the blocker in exception, ask a specific question, and explain
what the answer will enable. Ordinary unfinished work is not a human blocker.
For a blocker, goal_status may be omitted.
Human guidance in the assignment applies to the original goal.
If assignment data includes correction, repair the reported format using the
existing observations. Preserve the outcome and do not repeat completed work
just to correct a report.
