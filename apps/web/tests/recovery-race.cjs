const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const source = name => ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src', name), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
let state = []; let refs = []; let stateCursor = 0; let refCursor = 0; let first = true;
const effects = [];
let resolveCompare;
const compareGate = new Promise(resolve => { resolveCompare = resolve; });
const react = {
  useState(initial) { const index = stateCursor++; if (first) state[index] = initial; return [state[index], next => { state[index] = typeof next === 'function' ? next(state[index]) : next; }]; },
  useRef(initial) { const index = refCursor++; if (first) refs[index] = { current: initial }; return refs[index]; },
  useEffect(effect) { if (first) effects.push(effect); },
};
const stateApi = { fetchSession: async () => ({ contract_version: '0.2.0', workspace_id: 'team-one', principal_id: 'editor', role: 'editor', development_only: true }) };
const base = { snapshot: { workspace_id: 'team-one', as_of: '2026-10-09T00:00:00Z', receipts: [{ receipt_id: 'r-C', arrival_at: '2026-10-10T09:00:00Z', quantity: '4', unit: 'kg', component_id: 'C' }], orders: [{ order_id: 'O-A', product_id: 'A', output_quantity: '2', output_unit: 'piece', resource_id: 'R', due_at: '2026-10-10T10:00:00Z' }], periods: [{ period_id: 'P1', resource_id: 'R', start: '2026-10-10T09:00:00Z', end: '2026-10-10T10:00:00Z' }, { period_id: 'P2', resource_id: 'R', start: '2026-10-10T10:00:00Z', end: '2026-10-10T11:00:00Z' }], inventory: [], products: [], resources: [{ resource_id: 'R', capacity_model: 'single_machine' }], routings: [{ product_id: 'A', resource_id: 'R', run_seconds_per_output_unit: '900', output_unit: 'piece' }], maintenance: [], baseline_assignments: [{ order_id: 'O-A', period_id: 'P1' }] }, scenario: {}, recovery: { assignments: [{ order_id: 'O-A', period_id: 'P2' }] } };
class RecoveryError extends Error { constructor(message, kind) { super(message); this.kind = kind; } }
const recoveryApi = { RecoveryError, createExample: (_name, workspace) => ({ ...base, snapshot: { ...base.snapshot, workspace_id: workspace } }), buildComparisonRequest: () => base, requestComparison: () => compareGate };
const page = {};
const customRequire = name => name === 'react' ? react
  : name === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
    : name === 'react-router-dom' ? { Link: 'a' } : name === './state' ? stateApi : name === './recovery' ? recoveryApi : require(name);
vm.runInNewContext(source('recovery-page.tsx'), { exports: page, require: customRequire, AbortController });
function render() { stateCursor = 0; refCursor = 0; const tree = page.RecoveryPage(); first = false; return tree; }
function find(node, predicate) {
  if (!node || typeof node !== 'object') return null;
  if (predicate(node)) return node;
  for (const child of [node.props?.children].flat(Infinity)) { const result = find(child, predicate); if (result) return result; }
  return null;
}
(async () => {
  render(); effects.forEach(effect => effect()); await new Promise(setImmediate);
  const ready = render();
  assert.ok(find(ready, node => node.type === 'form' && node.props['aria-label'] === 'Recovery comparison inputs'), 'authenticated comparison form appears');
  assert.ok(find(ready, node => node.type === 'li' && String(node.props.children).includes('single machine')), 'declared resource capacity model is visible');
  recoveryApi.buildComparisonRequest = () => { throw new RecoveryError('Enter a complete UTC arrival.', 'validation'); };
  await find(ready, node => node.type === 'form').props.onSubmit({ preventDefault() {} });
  const invalid = render();
  assert.ok(find(invalid, node => node.type === 'strong' && node.props.children === 'Review comparison inputs'), 'local validation must not be called service unavailability');
  recoveryApi.buildComparisonRequest = () => base;
  const pending = find(invalid, node => node.type === 'form').props.onSubmit({ preventDefault() {} });
  const loading = render();
  assert.ok(find(loading, node => node.props?.role === 'status' && String(node.props.children).includes('Comparing')), 'loading visible');
  find(loading, node => node.type === 'input' && node.props.name === 'r-C').props.onChange({ target: { value: '2026-10-10T10:00:00Z' } });
  resolveCompare({ kind: 'compared', response: { comparison: { status: 'compared', original_baseline: { orders: [] }, disrupted_baseline: { orders: [] }, recovery: { orders: [] } } } });
  await pending;
  await new Promise(setImmediate);
  const edited = render();
  assert.equal(find(edited, node => node.props?.['data-result'] === 'comparison'), null, 'late result cannot repopulate after edit');
  recoveryApi.requestComparison = async () => ({ kind: 'unsupported', response: { reason: 'pre_as_of' } });
  await find(edited, node => node.type === 'form').props.onSubmit({ preventDefault() {} });
  const unsupported = render();
  assert.ok(find(unsupported, node => node.type === 'p' && String(node.props.children).includes('precedes the snapshot date')), 'unsupported date reason must be understandable');
  stateApi.fetchSession = async () => { throw new Error('Session denied'); };
  find(unsupported, node => node.type === 'button' && node.props.children === 'Retry session').props.onClick();
  await new Promise(setImmediate);
  const denied = render();
  assert.equal(find(denied, node => node.type === 'form'), null, 'session loss removes prior workspace inputs');
  console.log('recovery edit invalidates late result; session loss clears comparison');
})().catch(error => { console.error(error); process.exitCode = 1; });
