import {Lexer} from './marked.mjs';

// Build the transcript from Markdown tokens, never from model-supplied HTML.
// Images remain labeled links so inspecting a saved message makes no requests.
export function markdown(document, source) {
  const root = document.createElement('div');
  root.className = 'message-text message-markdown';
  const make = (tag, text) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    return node;
  };
  const decode = text => String(text).replace(/&(?:#\d+|#x[\da-f]+|[a-z][a-z\d]+);/gi, entity => {
    const node = make('textarea');
    node.innerHTML = entity; // Only a single character reference can reach here.
    return node.value;
  });
  const append = (parent, tokens) => {
    for (const token of tokens) {
      let node;
      switch (token.type) {
        case 'space': case 'def': continue;
        case 'heading':
          node = make(`h${Math.min(6, token.depth + 3)}`);
          node.className = 'message-prose-heading';
          append(node, token.tokens); break;
        case 'paragraph': node = make('p'); append(node, token.tokens); break;
        case 'text':
          if (token.tokens) { append(parent, token.tokens); continue; }
          parent.append(document.createTextNode(decode(token.text))); continue;
        case 'escape': parent.append(document.createTextNode(token.text)); continue;
        case 'html': parent.append(document.createTextNode(token.raw)); continue;
        case 'strong': case 'em': case 'del':
          node = make(token.type); append(node, token.tokens); break;
        case 'codespan': node = make('code', token.text); break;
        case 'code':
          node = make('pre'); node.append(make('code', token.text)); break;
        case 'hr': case 'br': node = make(token.type); break;
        case 'blockquote': node = make('blockquote'); append(node, token.tokens); break;
        case 'list':
          node = make(token.ordered ? 'ol' : 'ul');
          if (token.ordered) node.start = token.start;
          for (const item of token.items) {
            const row = make('li');
            append(row, item.tokens); node.append(row);
          }
          break;
        case 'checkbox':
          node = make('input'); node.type = 'checkbox';
          node.disabled = true; node.checked = token.checked; break;
        case 'table': {
          node = make('div'); node.className = 'message-table-scroll';
          const table = make('table'), head = make('thead'), body = make('tbody');
          const row = (cells, tag) => {
            const tr = make('tr');
            for (const cell of cells) {
              const td = make(tag);
              if (tag === 'th') td.scope = 'col';
              append(td, cell.tokens); tr.append(td);
            }
            return tr;
          };
          head.append(row(token.header, 'th'));
          for (const cells of token.rows) body.append(row(cells, 'td'));
          table.append(head, body); node.append(table); break;
        }
        case 'link': case 'image': {
          const href = decode(token.href);
          let url;
          try { url = new URL(href); } catch {}
          node = make(url && ['https:', 'http:', 'mailto:'].includes(url.protocol) ? 'a' : 'span');
          if (node.tagName === 'A') {
            node.href = url.href; node.target = '_blank'; node.rel = 'noopener noreferrer';
          }
          if (token.type === 'image') node.textContent = `[Image: ${decode(token.text) || href}]`;
          else append(node, token.tokens);
          if (token.title) node.title = decode(token.title);
          break;
        }
        default: parent.append(document.createTextNode(token.raw ?? token.text ?? '')); continue;
      }
      parent.append(node);
    }
  };
  try { append(root, Lexer.lex(source)); }
  catch { root.textContent = source; } // Incomplete streamed text still stays readable.
  return root;
}
