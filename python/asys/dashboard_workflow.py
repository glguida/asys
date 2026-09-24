"""Project saved BPMN connections for display without interpreting execution."""
import xml.etree.ElementTree as ET

BPMN = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
SCOPES = {'process', 'subProcess', 'adHocSubProcess', 'transaction'}


def graph(source, worker_kinds):
    root = ET.fromstring(source)

    def kind(element):
        namespace, separator, name = element.tag.rpartition('}')
        return name if not separator or namespace == '{' + BPMN else ''

    elements = [element for element in root.iter() if kind(element)]
    parents = {child: parent for parent in root.iter() for child in parent}
    edges, endpoints = [], set()
    for element in elements:
        if kind(element) != 'sequenceFlow':
            continue
        source, target = element.get('sourceRef'), element.get('targetRef')
        endpoints.update((source, target))
        condition = next((''.join(child.itertext()).strip() for child in element
                          if kind(child) == 'conditionExpression'), None)
        edges.append({'id': element.get('id'), 'name': element.get('name'),
                      'source': source, 'target': target, 'condition': condition})

    nodes, attachments = [], []
    for element in elements:
        name, identity = kind(element), element.get('id')
        # Connections establish graph membership, including future BPMN node
        # types. The categories also retain unconnected executable activities.
        if not identity or not (identity in endpoints or name.endswith(('Task', 'Event', 'Gateway', 'Activity'))
                                or name in {'task', 'subProcess', 'adHocSubProcess', 'transaction'}):
            continue
        node = {'id': identity, 'name': element.get('name') or identity, 'type': name}
        for field in ('default', 'attachedToRef', 'calledElement'):
            if element.get(field):
                node['defaultFlow' if field == 'default' else field] = element.get(field)
        scope = parents.get(element)
        while scope is not None and kind(scope) not in SCOPES:
            scope = parents.get(scope)
        if scope is not None and scope.get('id'):
            node['scope'] = scope.get('id')
        events = [kind(child) for child in element if kind(child).endswith('EventDefinition')]
        if events:
            node['eventDefinitions'] = events
        # Only this activity's binding applies: a subprocess must never inherit
        # the first nested task's worker type.
        binding = element.find('./{*}extensionElements/{urn:asys:workflow:1}job')
        if binding is not None:
            node['workerType'] = binding.get('type')
            node['workerKind'] = worker_kinds.get(binding.get('type'))
        nodes.append(node)
        if node.get('attachedToRef'):
            attachments.append({'id': f'attachment:{identity}', 'source': node['attachedToRef'],
                                'target': identity, 'kind': 'attachment'})

    return {'source': 'saved-definition', 'nodes': nodes, 'edges': edges, 'attachments': attachments}
