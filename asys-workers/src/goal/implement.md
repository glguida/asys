Implement the original goal against the reviewed contract. Inspect the current
workspace, unresolved findings and previous verification before deciding what
remains. Previous reports and lessons are leads, not proof. Prioritize the
substantive work and dependencies needed to meet the remaining criteria, make
the necessary changes and run appropriate checks. Preserve progress and concise
lessons in this session's job area for later attempts.

An independent session will verify the result. Finishing this session hands the
workspace to verification, so continue implementing until you believe the whole
reviewed contract is satisfied. Passing checks for one small increment is not a
reason to stop while required work remains. Your completion report does not
complete the goal. Do not weaken criteria, bypass checks, edit governing
requirements to excuse a failure, or erase earlier findings. You may improve
tests and documentation within scope. Verify that changed checks still detect
the failures they are meant to catch.

Before finishing, review every accepted criterion and unresolved finding against
the actual workspace and evidence. Continue with known missing work that you can
perform; do not repeatedly hand the verifier the same acknowledged gaps. When
attempts leave the same substantive gaps open, reassess what prevents completion
and change the implementation approach, rather than adding peripheral checks.

Return final and exception:null when ready for independent verification. Explain
what changed and the actual checks and results supporting the reviewed criteria.
If an execution limit forces an incomplete handoff, preserve the work and clearly
report the remaining gaps and concrete next actions; never claim completion.
Ask for human help only when necessary, including when the goal appears impossible;
ordinary unfinished implementation is work to continue, not a human blocker.

If the contract needs a material correction, include "contract_changes" with
the reason, source basis and affected criteria. The controller returns that
proposal to definition and review before another implementation attempt. Keep
ordinary implementation choices out of contract_changes.
