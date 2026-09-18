import { SequenceFlow } from 'bpmn-elements';

// A dependency enables a coordinator selection without starting its target.
// Native flow evaluation still decides whether take or discard is called.
export class AsysAdHocFlow extends SequenceFlow {
  constructor(definition, context, enable) {
    super(definition, context);
    this.context = context;
    this.enable = enable;
  }
  take(content) {
    // Synthetic inbound flows only prevent automatic starts. Completing the
    // coordinator must not manufacture selections or start control nodes.
    if (this.behaviour.asysSynthetic) return true;
    const { id, type, sourceId, targetId } = this;
    this.enable(this.context.getActivityById(targetId), { inbound: [{ ...content, id, type, sourceId, targetId }] });
    return true;
  }
  // Discarding one selection must not discard other, independently selectable
  // children. Dependency contract P5 pins completion without flow events.
  // Suppressed take/discard events also leave the native flow counters at zero
  // in checkpoints; they do not measure coordinator selections.
  discard() {}
}
