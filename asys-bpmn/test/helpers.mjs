export function workflow(body, { id = 'test', name = 'Test workflow' } = {}) {
  return `<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:asys="urn:asys:workflow:1"
    id="Definition_${id}" name="${name}" targetNamespace="urn:asys:test" expressionLanguage="feel">
    <bpmn:process id="${id}" isExecutable="true">${body}</bpmn:process></bpmn:definitions>`;
}
export function flow(id, from, to, condition = '') {
  return `<bpmn:sequenceFlow id="${id}" sourceRef="${from}" targetRef="${to}">${condition ? `<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression" language="feel">${condition}</bpmn:conditionExpression>` : ''}</bpmn:sequenceFlow>`;
}
export async function until(fn, { timeout = 10000 } = {}) {
  const deadline = Date.now() + timeout;
  for (;;) {
    const value = await fn();
    if (value) return value;
    if (Date.now() >= deadline) throw new Error('Timed out waiting for workflow state');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
