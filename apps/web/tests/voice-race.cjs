// Component-logic simulation of StrictMode effect replay; the coordinator runs the mounted browser check.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/voice-page.tsx'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const instances = [];
class FakeController {
  constructor() { this.disposed = false; this.refreshes = 0; instances.push(this); }
  subscribe(listener) { this.listener = listener; return () => { this.listener = undefined; }; }
  async refreshIdentity() { if (!this.disposed) { this.refreshes++; this.listener?.({ kind: 'idle', example: 'recovery', arrivals: {}, assignments: {} }); } }
  dispose() { this.disposed = true; }
}
let effect;
const refs = [];
let refCursor = 0;
let viewState;
const react = {
  useState(initial) { if (viewState === undefined) viewState = initial; return [viewState, next => { viewState = next; }]; },
  useRef(initial) { const index = refCursor++; return refs[index] ?? (refs[index] = { current: initial }); },
  useEffect(callback) { effect = callback; },
};
const listeners = new Map();
const page = { exports: {} };
const customRequire = name => name === 'react' ? react
  : name === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
    : name === 'react-router-dom' ? { Link: () => null }
      : name === './recovery-page' ? { Comparison: () => null }
        : name === './voice' ? { VoiceController: FakeController }
          : name.endsWith('.css') ? {} : require(name);
vm.runInNewContext(source, { exports: page.exports, require: customRequire,
  window: { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) } });
const render = () => { refCursor = 0; return page.exports.VoicePage(); };
const find = (node, predicate) => {
  if (!node || typeof node !== 'object') return null;
  if (predicate(node)) return node;
  for (const child of [node.props?.children].flat(Infinity)) { const found = find(child, predicate); if (found) return found; }
  return null;
};
render();
const firstCleanup = effect();
assert.equal(instances.length, 1);
firstCleanup();
assert.equal(instances[0].disposed, true);
const secondCleanup = effect();
assert.equal(instances.length, 2, 'StrictMode replay must create a fresh controller');
assert.equal(instances[1].disposed, false);
assert.equal(instances[1].refreshes, 1);
listeners.get('focus')();
assert.equal(instances[1].refreshes, 2);
instances[1].listener({ kind: 'failed', example: 'recovery', arrivals: {}, assignments: {}, message: 'Could not confirm cleanup of the previous local session. Check the session before continuing.' });
const failed = render();
assert.equal(find(failed, node => node.type === 'form'), null, 'unknown identity must keep the form hidden');
const retry = find(failed, node => node.type === 'button' && node.props.children === 'Check session again');
assert.ok(retry, 'failed cleanup without identity must keep explicit retry reachable');
retry.props.onClick();
assert.equal(instances[1].refreshes, 3);
secondCleanup();
assert.equal(instances[1].disposed, true);
console.log('voice component-logic StrictMode replay and failed-cleanup retry remain reachable');
