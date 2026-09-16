import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { chromium } from 'playwright-core';
import { indexElements, parseWorkflow, validateExecutable } from '../../src/bpmn.mjs';

const require = createRequire(import.meta.url);
const examples = [
  { path: 'shared-workspace/workflow.bpmn', task: 'assemble', elements: ['start', 'prepare', 'fork', 'left', 'right', 'join', 'assemble', 'end', 'begin', 'split', 'to_left', 'to_right', 'from_left', 'from_right', 'assemble_all', 'finish'] },
  { path: 'hello/workflow.bpmn', task: 'greet', elements: ['start', 'greet', 'end', 'begin', 'finish'] },
  { path: 'agent-task.bpmn', task: 'work', elements: ['start', 'work', 'end', 'begin', 'finish'] },
  { path: 'agent-review.bpmn', task: 'draft', elements: ['start', 'work', 'draft', 'approval', 'end', 'ready_for_review', 'begin', 'finish'] },
];

test('examples render and retain their execution bindings through bpmn.io editing', { timeout: 60_000 }, async t => {
  const browser = await chromium.launch(process.env.BPMN_BROWSER
    ? { executablePath: process.env.BPMN_BROWSER }
    : { channel: 'chrome' });
  t.after(() => browser.close());

  for (const example of examples) {
    await t.test(example.path, async t => {
      const xml = await readFile(new URL(`../../examples/${example.path}`, import.meta.url), 'utf8');
      const original = validateExecutable(await parseWorkflow(xml));
      const page = await browser.newPage({ viewport: { width: 1200, height: 640 } });
      t.after(() => page.close());
      await page.setContent('<style>html, body, #canvas { width: 100%; height: 100%; margin: 0; }</style><div id="canvas"></div>');
      for (const css of ['diagram-js.css', 'bpmn-js.css', 'bpmn-font/css/bpmn-embedded.css']) {
        await page.addStyleTag({ path: require.resolve(`bpmn-js/dist/assets/${css}`) });
      }
      await page.addScriptTag({ path: require.resolve('bpmn-js/dist/bpmn-modeler.development.js') });

      const edited = await page.evaluate(async ({ xml, task, elements }) => {
        // Deliberately use the ordinary modeler without an asys extension plugin.
        const modeler = new window.BpmnJS({ container: '#canvas' });
        const { warnings } = await modeler.importXML(xml);
        modeler.get('canvas').zoom('fit-viewport');
        const registry = modeler.get('elementRegistry');
        const rendered = elements.filter(id => {
          const element = registry.get(id);
          return element && !element.hidden && registry.getGraphics(element)
            ?.querySelector('.djs-visual')?.children.length;
        });
        const shape = registry.get(task);
        const label = `${shape.businessObject.name} (edited)`;
        const oldY = shape.y;
        modeler.get('modeling').updateProperties(shape, { name: label });
        modeler.get('modeling').moveShape(shape, { x: 0, y: 20 });
        const moved = shape.y === oldY + 20;
        const saved = await modeler.saveXML({ format: true });
        const reopened = await modeler.importXML(saved.xml);
        modeler.get('canvas').zoom('fit-viewport');
        const { svg } = await modeler.saveSVG();
        const svgDocument = new DOMParser().parseFromString(svg, 'image/svg+xml');
        const svgText = [...svgDocument.querySelectorAll('text')].map(text => text.textContent).join(' ');
        return {
          xml: saved.xml, svgText, label, moved, rendered,
          warnings: [...warnings, ...reopened.warnings].map(w => w.message),
        };
      }, { xml, ...example });

      assert.deepEqual(edited.warnings, []);
      assert.deepEqual(edited.rendered, example.elements, 'all tasks, events, and connectors must be drawn');
      assert.equal(edited.moved, true);
      assert.ok(edited.svgText.replace(/\s/gu, '').includes(edited.label.replace(/\s/gu, '')),
        'the edited label must appear in the drawing');
      const exported = validateExecutable(await parseWorkflow(edited.xml));
      assert.deepEqual(exported.bindings, original.bindings, 'prompts, arguments, and job types must survive editing');
      assert.deepEqual(exported.processes, original.processes);
      const before = indexElements(original.document);
      const after = indexElements(exported.document);
      assert.equal(after.get(example.task).name, edited.label);
      for (const id of example.elements) {
        for (const property of ['$type', 'script', 'documentation', 'sourceRef', 'targetRef']) {
          assert.deepEqual(after.get(id)[property], before.get(id)[property], `${id}.${property}`);
        }
      }
      if (process.env.BPMN_TEST_OUTPUT) {
        await mkdir(process.env.BPMN_TEST_OUTPUT, { recursive: true });
        const target = join(process.env.BPMN_TEST_OUTPUT, example.path.replaceAll('/', '-'));
        await writeFile(target, edited.xml);
        await page.screenshot({ path: `${target}.png` });
      }

      if (example.path === 'hello/workflow.bpmn') {
        // This is the original defect: valid process XML without a displayable diagram.
        const withoutDiagram = xml.replace(/\s*<bpmndi:BPMNDiagram\b[^]*?<\/bpmndi:BPMNDiagram>/u, '');
        const message = await page.evaluate(async xml => {
          const modeler = new window.BpmnJS({ container: '#canvas' });
          try { await modeler.importXML(xml); return ''; }
          catch (error) { return error.message; }
          finally { modeler.destroy(); }
        }, withoutDiagram);
        assert.match(message, /no diagram to display/iu);
      }
    });
  }
});
