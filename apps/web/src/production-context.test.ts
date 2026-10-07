import { describe, expect, it } from 'vitest';
import { contextRefMatches, savedScenario, parseSnapshot } from './production-context';
import { voiceExample } from './voice';

describe('saved synthetic production context', () => {
  it('accepts a complete snapshot without rounding decimal strings and starts unchanged', () => {
    const snapshot = voiceExample('recovery_precision', 'W').snapshot;
    const parsed = parseSnapshot(JSON.stringify(snapshot), 'W');
    expect(parsed).toEqual(snapshot);
    const scenario = savedScenario(parsed);
    expect(scenario.scenario.arrival_replacements).toEqual([]);
    expect(scenario.recovery.assignments).toEqual(snapshot.baseline_assignments);
  });

  it('rejects incomplete, foreign and unsafe snapshot JSON', () => {
    const snapshot = voiceExample('recovery', 'W').snapshot;
    expect(() => parseSnapshot('{', 'W')).toThrow();
    expect(() => parseSnapshot(JSON.stringify({ ...snapshot, receipts: undefined }), 'W')).toThrow();
    expect(() => parseSnapshot(JSON.stringify(snapshot), 'other')).toThrow();
    expect(() => parseSnapshot(JSON.stringify({ ...snapshot, extra: true }), 'W')).toThrow();
  });

  it('matches the complete server reference including safe revision', () => {
    const ref = { context_id: 'a'.repeat(32), workspace_id: 'W', revision: 1 };
    expect(contextRefMatches(ref, ref)).toBe(true);
    expect(contextRefMatches({ ...ref, revision: 2 }, ref)).toBe(false);
    expect(contextRefMatches({ ...ref, revision: 1.5 }, ref)).toBe(false);
    expect(contextRefMatches({ ...ref, revision: Number.MAX_SAFE_INTEGER + 1 }, ref)).toBe(false);
  });
});
