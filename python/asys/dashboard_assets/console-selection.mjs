// Native selection autoscroll can move the document instead of a nested log.
// Own only primary-mouse text drags inside this box; leave controls and touch
// interactions to the browser. No page styles or scroll positions are locked.
export function installConsoleSelection(element, {onSelectionStart = () => {}} = {}) {
  const document = element.ownerDocument, window = document.defaultView;
  let drag = null, animation = 0;
  const controls = 'a,button,input,textarea,select,summary,[contenteditable]:not([contenteditable="false"]),[role="button"]';
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

  function caret(x, y) {
    const rect = element.getBoundingClientRect();
    x = clamp(x, rect.left + element.clientLeft + 2, rect.left + element.clientLeft + element.clientWidth - 2);
    y = clamp(y, rect.top + element.clientTop + 2, rect.top + element.clientTop + element.clientHeight - 2);
    const position = document.caretPositionFromPoint?.(x, y);
    const range = position ? null : document.caretRangeFromPoint?.(x, y);
    const node = position?.offsetNode ?? range?.startContainer;
    const offset = position?.offset ?? range?.startOffset;
    return node && element.contains(node) ? {node, offset} : null;
  }

  function extend() {
    const focus = caret(drag.x, drag.y);
    if (!focus || !element.contains(drag.anchor.node)) return;
    document.getSelection()?.setBaseAndExtent(drag.anchor.node, drag.anchor.offset, focus.node, focus.offset);
  }

  function tick(time) {
    if (!drag || !element.isConnected) { stop(); return; }
    const elapsed = Math.min(50, time - drag.time);
    drag.time = time;
    if (drag.selecting) {
      const rect = element.getBoundingClientRect(), edge = Math.min(28, element.clientHeight / 4);
      const top = rect.top + element.clientTop, bottom = top + element.clientHeight;
      let speed = 0;
      if (drag.y < top + edge) speed = -Math.min(1100, 100 + (top + edge - drag.y) * 12);
      else if (drag.y > bottom - edge) speed = Math.min(1100, 100 + (drag.y - bottom + edge) * 12);
      if (speed) { element.scrollTop += speed * elapsed / 1000; extend(); }
    }
    animation = window.requestAnimationFrame(tick);
  }

  function move(event) {
    if (!drag) return;
    if (!(event.buttons & 1)) { stop(); return; }
    event.preventDefault();
    drag.x = event.clientX; drag.y = event.clientY;
    if (!drag.selecting && Math.hypot(drag.x - drag.startX, drag.y - drag.startY) >= 3) {
      drag.selecting = true;
      onSelectionStart();
    }
    if (drag.selecting) extend();
  }

  function stop() {
    window.cancelAnimationFrame(animation); animation = 0; drag = null;
    window.removeEventListener('mousemove', move, true);
    window.removeEventListener('mouseup', stop, true);
    window.removeEventListener('blur', stop);
  }

  function start(event) {
    if (event.button !== 0 || event.detail > 1 || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target.closest?.(controls)) return;
    const rect = element.getBoundingClientRect(), left = rect.left + element.clientLeft, top = rect.top + element.clientTop;
    if (event.clientX < left || event.clientX >= left + element.clientWidth
      || event.clientY < top || event.clientY >= top + element.clientHeight) return;
    const point = caret(event.clientX, event.clientY), selection = document.getSelection();
    if (!point || !selection) return;
    stop();
    const anchor = event.shiftKey && element.contains(selection.anchorNode)
      ? {node: selection.anchorNode, offset: selection.anchorOffset} : point;
    // Suppress the native drag from its start, including its page autoscroll.
    event.preventDefault();
    element.focus({preventScroll: true});
    selection.setBaseAndExtent(anchor.node, anchor.offset, point.node, point.offset);
    drag = {anchor, x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY,
      selecting: event.shiftKey && !selection.isCollapsed, time: window.performance.now()};
    if (drag.selecting) onSelectionStart();
    window.addEventListener('mousemove', move, {capture: true, passive: false});
    window.addEventListener('mouseup', stop, true);
    window.addEventListener('blur', stop);
    animation = window.requestAnimationFrame(tick);
  }

  element.addEventListener('mousedown', start);
  return () => { stop(); element.removeEventListener('mousedown', start); };
}
