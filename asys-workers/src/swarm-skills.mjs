import { existsSync, lstatSync, readFileSync, readdirSync, realpathSync } from 'node:fs';
import { join, relative, isAbsolute, sep } from 'node:path';

const FILE_BYTES = 16 * 1024;
const TOTAL_BYTES = 64 * 1024;
const MAX_SKILLS = 16;

export function inside(root, path) {
  const local = relative(root, realpathSync(path));
  if (local === '..' || local.startsWith(`..${sep}`) || isAbsolute(local)) {
    throw new Error('Swarm instructions must remain inside their declared resource directory');
  }
}

export function instructionFile(root, path, maximum = 32 * 1024) {
  if (!existsSync(path)) return '';
  const stat = lstatSync(path);
  if (stat.isSymbolicLink() || !stat.isFile()) throw new Error(`Swarm instruction must be a regular file: ${path}`);
  inside(root, path);
  if (stat.size > maximum) throw new Error(`Swarm instruction exceeds ${maximum} bytes: ${path}`);
  const text = readFileSync(path, 'utf8');
  if (Buffer.byteLength(text, 'utf8') > maximum) throw new Error(`Swarm instruction exceeds ${maximum} bytes: ${path}`);
  return text.trim();
}

export function swarmSkills(roots) {
  const loaded = [], seen = new Set();
  let bytes = 0;
  for (const owner of [...new Set(roots)]) {
    const root = join(owner, 'skills');
    if (!existsSync(root)) continue;
    if (lstatSync(root).isSymbolicLink() || !lstatSync(root).isDirectory()) {
      throw new Error('Swarm skills must be a regular resource directory');
    }
    inside(owner, root);
    const files = existsSync(join(root, 'SKILL.md')) ? [join(root, 'SKILL.md')] : [];
    for (const entry of readdirSync(root, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (entry.name.startsWith('.')) continue;
      if (entry.isSymbolicLink()) throw new Error('Swarm skill directories must not use symlinks');
      if (entry.isDirectory() && existsSync(join(root, entry.name, 'SKILL.md'))) files.push(join(root, entry.name, 'SKILL.md'));
    }
    for (const path of files) {
      if (seen.has(path)) continue;
      seen.add(path);
      if (seen.size > MAX_SKILLS) throw new Error(`Swarm instructions support at most ${MAX_SKILLS} skills`);
      const text = instructionFile(root, path, FILE_BYTES);
      bytes += Buffer.byteLength(text, 'utf8');
      if (bytes > TOTAL_BYTES) throw new Error(`Swarm skill instructions exceed ${TOTAL_BYTES} bytes`);
      loaded.push({ path, text });
    }
  }
  return loaded;
}
