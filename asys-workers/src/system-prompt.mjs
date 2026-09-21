import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const instructions = readFileSync(new URL('system.md', import.meta.url), 'utf8').trim();
const reportingGuide = fileURLToPath(new URL('reporting-to-humans.md', import.meta.url));

export function workerPrompt({ definition, jobDirectory, workspace }) {
  return {
    systemPrompt: instructions,
    appendSystemPrompt: [
      `When the assignment calls for a structured human decision request or review briefing, read ${JSON.stringify(reportingGuide)} for its reporting guidance.`,
      `Agent: ${definition.name}. Its definition is in ${JSON.stringify(definition.directory)}. Read its resources as needed; do not modify the agent definition or environment during this job.`,
      definition.tools && `Installed environment tools:\n${definition.tools}`,
      definition.prompt && `Agent instructions:\n${definition.prompt}`,
      definition.memory && `Agent memory:\n${definition.memory}`,
      `Job area: ${JSON.stringify(jobDirectory)}. Keep temporary files in scratch/ there. Write your factual report to report.md there, and any concise lessons supported by this job to lessons.md there. Lessons are proposals for later review, not changes to agent memory.`,
      `Workspace: ${JSON.stringify(workspace)}. This is your working directory and contains the material you are assigned to work on. Follow the task's instructions for deliverables.`,
    ].filter(Boolean),
    extensionFactories: [{ name: 'asys-worker-tools', factory(pi) {
      // Pi supplies the registered tools and their guidance. Keep its resource
      // discovery and extension prompt additions without its interactive role.
      pi.on('before_agent_start', event => {
        const active = new Set(pi.getActiveTools());
        const tools = pi.getAllTools().filter(tool => active.has(tool.name));
        const snippets = event.systemPromptOptions.toolSnippets ?? {};
        const available = tools.map(tool => `- ${tool.name}: ${(snippets[tool.name] || tool.description).replace(/\s+/g, ' ').trim()}`);
        const guidelines = new Set(tools.flatMap(tool => tool.promptGuidelines ?? []));
        if (active.has('bash') && !['grep', 'find', 'ls'].some(name => active.has(name))) {
          guidelines.add('Use bash for file operations such as ls, rg, and find, and to run installed programs.');
        }
        const guidance = [...guidelines].map(line => `- ${line}`).join('\n');
        return { systemPrompt: [event.systemPrompt, `Available tools:\n${available.join('\n') || '(none)'}`,
          guidance && `Tool use:\n${guidance}`].filter(Boolean).join('\n\n') };
      });
    } }],
  };
}
