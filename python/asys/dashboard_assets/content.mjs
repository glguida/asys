// Recognize complete JSON blocks within prose without interpreting code fences
// or changing the original message, which remains available as plain text.
export function contentParts(source) {
  if (typeof source !== 'string') return [{kind: 'data', value: source}];
  const parts = []; let textStart = 0, offset = 0, fence = null;
  while (offset < source.length) {
    const end = source.indexOf('\n', offset), lineEnd = end < 0 ? source.length : end + 1;
    const line = source.slice(offset, lineEnd);
    const marker = line.match(/^ {0,3}(`{3,}|~{3,})([^\n]*)/);
    if (marker) {
      if (fence) { if (marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = null; }
      else if (marker[2].trim().toLowerCase() === 'json') {
        const closing = new RegExp('^ {0,3}' + marker[1][0] + '{' + marker[1].length + ',}\\s*$', 'm');
        const rest = source.slice(lineEnd), match = closing.exec(rest);
        if (match) {
          try {
            const value = JSON.parse(rest.slice(0, match.index));
            if (value !== null && typeof value === 'object') {
              if (offset > textStart) parts.push({kind: 'text', value: source.slice(textStart, offset)});
              parts.push({kind: 'data', value});
              offset = lineEnd + match.index + match[0].length; textStart = offset; continue;
            }
          } catch {}
        }
        fence = marker[1];
      } else fence = marker[1];
      offset = lineEnd; continue;
    }
    const beginning = !fence && line.match(/^[ \t]*[\[{]/);
    if (beginning) {
      const start = offset + beginning[0].length - 1;
      let depth = 0, quoted = false, escaped = false, finish = -1;
      for (let i = start; i < source.length; i++) {
        const char = source[i];
        if (quoted) { if (escaped) escaped = false; else if (char === '\\') escaped = true; else if (char === '"') quoted = false; }
        else if (char === '"') quoted = true;
        else if (char === '{' || char === '[') depth++;
        else if (char === '}' || char === ']') { if (--depth === 0) { finish = i + 1; break; } }
      }
      if (finish > start) {
        const remainder = source.slice(finish, source.indexOf('\n', finish) < 0 ? source.length : source.indexOf('\n', finish));
        if (!remainder.trim()) try {
          const value = JSON.parse(source.slice(start, finish));
          if (offset > textStart) parts.push({kind: 'text', value: source.slice(textStart, offset)});
          parts.push({kind: 'data', value}); offset = finish; textStart = finish; continue;
        } catch {}
      }
    }
    offset = lineEnd;
  }
  if (textStart < source.length) parts.push({kind: 'text', value: source.slice(textStart)});
  return parts.filter(part => part.kind === 'data' || part.value.trim());
}

export function outputRecords(payload) {
  const records = [], data = payload.data ?? {};
  const result = data.result ?? data[payload.kind]?.result;
  if (result !== undefined) records.push({id: 'job-result', title: 'Saved result', type: 'result', data: result});
  for (const [index, session] of (data[payload.kind]?.sessions ?? []).entries()) {
    if (session.result === undefined && !session.error) continue;
    records.push({id: `session-${session.id ?? index}`, title: [session.participant ?? `Attempt ${session.attempt ?? index+1}`, session.phase, session.round == null ? null : `round ${session.round}`].filter(Boolean).join(' · '),
      type: 'result', data: session.result ?? {error: session.error}});
  }
  records.push(...(payload.output?.records ?? []));
  if (!records.length && payload.output?.text) records.push({id: 'saved-output', title: payload.output.title ?? 'Saved output', type: 'text', text: payload.output.text});
  if (!records.length && payload.kind === 'program' && payload.transcript?.text)
    records.push({id: 'saved-output', title: 'Program output', type: 'text', text: payload.transcript.text});
  for (const stream of ['stdout', 'stderr']) if (data.logs?.[stream])
    records.push({id: `job-${stream}`, title: `Execution log · ${stream}`, type: 'stream', stream, text: data.logs[stream]});
  if (payload.output?.notice) records.unshift({id: 'notice', title: 'Available evidence', type: 'notice', text: payload.output.notice});
  return records;
}
