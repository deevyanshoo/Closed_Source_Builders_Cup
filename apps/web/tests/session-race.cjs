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
const stateApi = { fetchSession: () => sessionGate, listPlans: async () => ({ plans: [] }) };
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
  assert.ok(find(after, node => node.type === 'p' && node.props.children === 'Workspace local-workspace · editor access'), 'session identity must appear after editing during session load');
  console.log('session response survives an input edit and keeps the edited field');
})().catch(error => { console.error(error); process.exitCode = 1; });
