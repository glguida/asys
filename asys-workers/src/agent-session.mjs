import { makeDirectory, prepareFile } from '../../asys-runtime/javascript/permissions.mjs';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { createAgentSession, DefaultResourceLoader, SessionManager, SettingsManager } from '@earendil-works/pi-coding-agent';
import { clone, requiredString } from './values.mjs';
import { agentResult } from './agent-definition.mjs';
import { workerPrompt } from './system-prompt.mjs';
import { agentModel } from './agent-model.mjs';

// Pi owns the agent loop, local tools, context management, and session format.
// Each job starts a new session; snapshots are its transcript.
export async function runAgent({ config, job, signal, provider, workspace: cwd, jobDirectory, definition, extensionPaths = [], save, event, customTools = [] }) {
  signal.throwIfAborted();
  const agentDir = join(jobDirectory, '.pi');
  makeDirectory(agentDir, { recursive: true, mode: 0o700 });
  job.agent = {
    model: requiredString(config.model, `${job.id} model`),
    prompt: requiredString(config.prompt, `${job.id} prompt`),
    name: definition.name, environment: definition.environment, directory: definition.directory,
    promptHash: definition.promptHash, memoryHash: definition.memoryHash,
    steps: 0,
  };
  const state = job.agent;
  const sessionManager = SessionManager.inMemory(cwd);
  snapshot();
  const { modelRuntime, model } = await agentModel({ config, agentDir, provider, signal, event,
    beforeRequest(context) {
      if (config.maxSteps !== undefined && state.steps >= config.maxSteps) throw new Error(`${job.id} exceeded ${config.maxSteps} inference steps`);
      state.steps++;
      snapshot();
      const promptFile = join(agentDir, 'system-prompt.txt');
      prepareFile(promptFile);
      writeFileSync(promptFile, context.systemPrompt ?? '');
    },
  });
  const settingsManager = SettingsManager.inMemory({ compaction: { enabled: true } });
  const resourceLoader = new DefaultResourceLoader({ cwd, agentDir, settingsManager,
    noSkills: true, noExtensions: true, noContextFiles: true, noPromptTemplates: true, noThemes: true,
    additionalSkillPaths: definition.skills,
    additionalExtensionPaths: [...definition.extensions, ...extensionPaths],
    ...workerPrompt({ definition, jobDirectory, workspace: cwd }),
  });
  await resourceLoader.reload();
  const extensionErrors = resourceLoader.getExtensions().errors;
  if (extensionErrors.length) throw new Error(`Pi extension loading failed: ${extensionErrors.map(e => `${e.path}: ${e.error}`).join('; ')}`);
  const { session } = await createAgentSession({ cwd, agentDir, modelRuntime, model,
    resourceLoader, sessionManager, settingsManager, customTools });
  const onAbort = () => { void session.abort(); };
  signal.addEventListener('abort', onAbort, { once: true });
  let compactionError;
  const unsubscribe = session.subscribe(e => {
    if (signal.aborted) return;
    if (e.type === 'message_start' && e.message.role === 'assistant') {
      // The session appends a message only when it ends. Observers use this
      // parent to replace the live text when the saved session catches up.
      event('agent.message_started', { parentId: sessionManager.getLeafId() });
    } else if (e.type === 'message_update') {
      const update = e.assistantMessageEvent;
      if (update.type === 'text_delta' || update.type === 'thinking_delta') {
        event('agent.message_delta', { kind: update.type === 'text_delta' ? 'text' : 'thinking',
          contentIndex: update.contentIndex, delta: update.delta });
      }
    } else if (e.type === 'tool_execution_start') {
      event('agent.tool_started', { name: e.toolName, callId: e.toolCallId });
      snapshot();
    } else if (e.type === 'tool_execution_end') {
      event('agent.tool_completed', { name: e.toolName, callId: e.toolCallId, isError: e.isError });
    } else if (e.type === 'auto_retry_start' || e.type === 'summarization_retry_scheduled') {
      event('agent.provider_retrying', { attempt: e.attempt, maxAttempts: e.maxAttempts,
        delayMs: e.delayMs, errorMessage: e.errorMessage });
    } else if (e.type === 'compaction_start') {
      compactionError = undefined;
      event('agent.compaction_started', { reason: e.reason });
    } else if (e.type === 'compaction_end') {
      compactionError = e.errorMessage;
      // Pi has appended the compaction entry. Persist it before retrying or
      // notifying observers, which may cancel the job at this boundary.
      snapshot();
      event('agent.compaction_ended', { reason: e.reason, aborted: e.aborted, willRetry: e.willRetry,
        errorMessage: e.errorMessage, tokensBefore: e.result?.tokensBefore, tokensAfter: e.result?.estimatedTokensAfter });
    }
    if (['message_end', 'tool_execution_end'].includes(e.type)) queueMicrotask(snapshot);
  });
  function snapshot() {
    if (signal.aborted) return;
    state.session = clone({ header: sessionManager.getHeader(), entries: sessionManager.getEntries(), leafId: sessionManager.getLeafId() });
    save();
  }
  try {
    let prompt = state.prompt;
    for (let correction = 0; ; correction++) {
      signal.throwIfAborted();
      await session.prompt(prompt, { expandPromptTemplates: false });
      signal.throwIfAborted();
      snapshot();
      const assistant = session.messages.at(-1);
      if (!isFinalAnswer(assistant)) throw new Error(compactionError || assistant?.errorMessage || `Agent ${job.id} did not finish`);
      try { return agentResult(assistant); }
      catch (error) {
        // Repair only the report, once, with the completed work still in context.
        if (correction) throw error;
        event('agent.result_correcting', { errorMessage: error.message });
        prompt = `Your final response was rejected: ${error.message}\n\n` +
          'Correct the response format using the work and observations already in this session. ' +
          'Return one valid JSON object with a nonempty string final, exception (null or a nonempty reason), ' +
          'and all task-specific result fields. Use double quotes for JSON strings; no Markdown fences or surrounding prose. ' +
          'Preserve the findings and outcome, including any blocker. Do not repeat completed work or run tools just to reformat the report.';
      }
    }
  } finally {
    signal.removeEventListener('abort', onAbort);
    unsubscribe();
    session.dispose();
  }
  function isFinalAnswer(message) {
    return message?.role === 'assistant' && message.stopReason === 'stop' && !message.content.some(c => c.type === 'toolCall');
  }
}
