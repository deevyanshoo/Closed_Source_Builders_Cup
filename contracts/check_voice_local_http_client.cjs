#!/usr/bin/env node
// Generated-client serialization checks only; fake transport proves no authentication or server behavior.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const tsc = process.argv[2];
if (!tsc || !fs.statSync(tsc).isFile()) throw new Error('Pass the existing trusted TypeScript compiler path');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-http-client-'));
const fixture = name => JSON.parse(fs.readFileSync(path.join(__dirname, 'examples', 'voice_http_' + name + '.json'), 'utf8'));
async function check() {
 const build = spawnSync(process.execPath, [tsc, '--outDir', temporary, '--strict', '--lib', 'es2022,dom', '--target', 'es2022', '--module', 'commonjs', '--moduleResolution', 'node', path.join(root, 'contracts/generated/voice-http-client.ts')], { encoding: 'utf8' });
 assert.equal(build.status, 0, build.stdout + build.stderr);
 const { createVoiceHTTPClient, VoiceHTTPError } = require(path.join(temporary, 'voice-http-client.js'));
 let expected;
 const calls = [];
 const transport = async (url, init) => { calls.push({ url, init }); return new Response(JSON.stringify(expected), { status: 200, headers: { 'Content-Type': 'application/json' } }); };
 const client = createVoiceHTTPClient('http://127.0.0.1:8816', () => 'synthetic-unusable-token', transport);
 const session = fixture('recovery_precision_session');
 expected = fixture('created');
 assert.deepEqual(await client.create(session), expected);
 assert.deepEqual(JSON.parse(calls.at(-1).init.body), session);
 const turn = fixture('recovery_precision_turn');
 expected = fixture('accepted');
 await client.start('session/with space', turn);
 assert.equal(calls.at(-1).url, 'http://127.0.0.1:8816/v2/voice/sessions/session%2Fwith%20space/turns');
 assert.deepEqual(JSON.parse(calls.at(-1).init.body), turn);
 for (const state of ['pending', 'recovery_precision_result', 'failed']) {
  expected = fixture(state);
  assert.deepEqual(await client.result('session', '1'), expected);
  assert.equal(calls.at(-1).init.method, 'GET');
  assert.equal(calls.at(-1).init.body, undefined);
 }
 expected = fixture('cancelled'); await client.cancel('session', '1');
 assert.equal(calls.at(-1).init.method, 'POST'); assert.equal(calls.at(-1).init.body, undefined);
 expected = fixture('closed'); await client.close('session'); assert.equal(calls.at(-1).init.method, 'DELETE');
 assert(calls.every(x => x.init.cache === 'no-store' && x.init.headers.Authorization === 'Bearer synthetic-unusable-token'));
 const failure = createVoiceHTTPClient('', () => 'synthetic-unusable-token', async () => new Response(JSON.stringify(fixture('unavailable')), { status: 503 }));
 await assert.rejects(failure.create(session), e => e instanceof VoiceHTTPError && e.status === 503 && e.response.error.code === 'voice_unavailable');
 console.log('Typed local HTTP client preserves exact precision request/result fixtures, state variants, encoded paths and semantic errors; transport stub only');
}
check().finally(() => fs.rmSync(temporary, { recursive: true, force: true })).catch(error => { console.error(error.message); process.exitCode = 1; });
