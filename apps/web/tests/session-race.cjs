const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = name => ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src', name), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const planning = { exports: {} };
vm.runInNewContext(source('planning.ts'), { exports: planning.exports, require });

let state = []; let refs = []; let stateCursor = 0; let refCursor = 0; let first = true;
const effects = [];
let releaseSession;
const sessionGate = new Promise(resolve => { releaseSession = resolve; });
const react = {
  useState(initial) { const index = stateCursor++; if (first) state[index] = initial; return [state[index], next => { state[index] = typeof next === 'function' ? next(state[index]) : next; }]; },
  useRef(initial) { const index = refCursor++; if (first) refs[index] = { current: initial }; return refs[index]; },
  useEffect(effect) { if (first) effects.push(effect); },
};
const stateApi = { fetchSession: () => sessionGate, listPlans: async () => { throw new Error('Saved-plan storage is offline.'); } };
planning.exports.fetchMaterials = async () => ({ materials: [{ key: 'steel', display_name: 'Steel', source_series: 's', source_unit: 'kg' }], availability: { code: 'available', reason: '' } });
const page = {};
const customRequire = name => name === 'react' ? react
  : name === 'react/jsx-runtime' ? { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
    : name === './planning' ? planning.exports : name === './state' ? stateApi : require(name);
vm.runInNewContext(source('page.tsx'), { exports: page, require: customRequire, AbortController });
function render() { stateCursor = 0; refCursor = 0; const tree = page.PlanningPage(); first = false; return tree; }
function find(node, predicate) {
  if (!node || typeof node !== 'object') return null;
  if (predicate(node)) return node;
  for (const child of [node.props?.children].flat(Infinity)) { const result = find(child, predicate); if (result) return result; }
  return null;
}

(async () => {
  render(); effects.forEach(effect => effect());
  await new Promise(setImmediate);
  const before = render();
  const buyer = find(before, node => node.type === 'input' && node.props.name === 'buyerId');
  assert.equal(find(before, node => node.type === 'fieldset').props.disabled, false);
  buyer.props.onChange({ target: { value: 'buyer-edited-before-session' } });
  releaseSession({ contract_version: '0.2.0', principal_id: 'dev-editor', workspace_id: 'local-workspace', role: 'editor', development_only: true });
  await new Promise(setImmediate);
  const after = render();
  assert.equal(find(after, node => node.type === 'input' && node.props.name === 'buyerId').props.value, 'buyer-edited-before-session');
  assert.ok(find(after, node => node.type === 'p' && typeof node.props.children === 'string' && node.props.children.includes('Workspace local-workspace') && node.props.children.includes('local development identity')), 'session identity must appear after editing during session load');
  assert.ok(find(after, node => node.type === 'strong' && node.props.children === 'Saved-plan storage unavailable'), 'failed plan listing must be labeled as storage failure');
  assert.ok(find(after, node => node.type === 'button' && node.props.children === 'Retry list →'), 'failed plan listing must offer list retry');
  assert.equal(find(after, node => node.type === 'button' && node.props.children === 'Retry session →'), null, 'valid identity must not be erased by storage failure');
  stateApi.fetchSession = async () => { throw new Error('Session denied.'); };
  effects[0]();
  await new Promise(setImmediate);
  const denied = render();
  assert.ok(find(denied, node => node.type === 'p' && node.props.children === 'Interactive sign-in unavailable. Saved plans require an authenticated session.'), 'actual session failure must clear the old identity');
  assert.equal(find(denied, node => node.type === 'button' && node.props.children === 'New plan'), null, 'old saved-plan controls must disappear');
  stateApi.fetchSession = async () => ({ contract_version: '0.2.0', principal_id: 'other-editor', workspace_id: 'other-workspace', role: 'editor', development_only: true });
  stateApi.listPlans = async () => ({ plans: [] });
  find(denied, node => node.type === 'button' && node.props.children === 'Retry session →').props.onClick();
  await new Promise(setImmediate);
  const switched = render();
  assert.ok(find(switched, node => node.type === 'p' && typeof node.props.children === 'string' && node.props.children.includes('Workspace other-workspace')), 'retry must show the new server session');
  assert.equal(find(switched, node => node.type === 'strong' && node.props.children === 'Saved-plan storage unavailable'), null, 'new session must clear prior storage error');
  console.log('session survives storage failure; actual session failure and switch clear old state');
})().catch(error => { console.error(error); process.exitCode = 1; });
