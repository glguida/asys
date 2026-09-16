import BpmnModdle from 'bpmn-moddle';
import { Reader } from 'moddle-xml';
import extension from './moddle.json' with { type: 'json' };

// XML 1.0 (Fifth Edition), NameStartChar/NameChar without ':': xs:ID is
// an NCName. moddle-xml 11's default registration uses an ASCII-only regexp
// and an object with inherited keys. Adapt this reader instance's ID registry
// so valid Unicode IDs and names such as "constructor" work as XML IDs.
const start = 'A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF\uF900-\uFDCF\uFDF0-\uFFFD\u{10000}-\u{EFFFF}';
const ncname = new RegExp(`^[${start}][${start}0-9.\\-\u00B7\u0300-\u036F\u203F-\u2040]*$`, 'u');

export function readBpmn(xml) {
  const reader = new Reader({ model: new BpmnModdle({ asys: extension }), lax: false });
  const root = reader.handler('bpmn:Definitions');
  let initialized = false;
  const wrapped = new WeakSet();
  function adapt(handler) {
    if (wrapped.has(handler)) return handler;
    wrapped.add(handler);
    const handleNode = handler.handleNode;
    handler.handleNode = function(node) {
      const context = this.context;
      context.namespaces = node.ns;
      if (!initialized) {
        initialized = true;
        context.targetNamespace = node.attributes.targetNamespace;
        context.elementsById = Object.create(null);
        context.addReference = function(reference) {
          const namespaces = this.namespaces;
          let id = localReference(reference.id, namespaces, this.targetNamespace);
          Object.defineProperty(reference, 'id', { enumerable: true,
            get: () => id,
            set: value => { id = localReference(value, namespaces, this.targetNamespace); },
          });
          this.references.push(reference);
        };
        context.addElement = function(element) {
          if (!element) throw new Error('Expected an XML element');
          if (element.$type === 'bpmn:CallActivity' && element.calledElement && !element.calledElement.trimStart().startsWith('=')) {
            element.calledElement = localReference(element.calledElement, this.namespaces, this.targetNamespace);
          }
          const property = element.$descriptor.idProperty;
          const id = property && element.get(property.name);
          if (!id) return;
          if (!ncname.test(id)) throw new Error(`Illegal XML ID <${id}>`);
          if (Object.hasOwn(this.elementsById, id)) throw new Error(`Duplicate ID <${id}>`);
          this.elementsById[id] = element;
        };
      }
      return adapt(handleNode.call(this, node));
    };
    return handler;
  }
  return reader.fromXML(xml, adapt(root));
}

function localReference(value, namespaces, targetNamespace) {
  const text = value.trim();
  const colon = text.indexOf(':');
  if (colon < 0) return text;
  const prefix = text.slice(0, colon);
  return namespaces?.[`${prefix}$uri`] === targetNamespace ? text.slice(colon + 1) : text;
}
