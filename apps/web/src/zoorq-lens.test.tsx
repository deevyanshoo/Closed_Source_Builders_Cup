import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ZoorqLens } from './zoorq-lens';

describe('ZoorqLens', () => {
  it('reflects voice state visually without claiming to control the microphone', () => {
    expect(renderToStaticMarkup(<ZoorqLens state="listening" compact />)).toContain('data-motion="active"');
    expect(renderToStaticMarkup(<ZoorqLens state="idle" />)).toContain('data-motion="static"');
    expect(renderToStaticMarkup(<ZoorqLens state="muted" />)).toContain('data-motion="static"');
    expect(renderToStaticMarkup(<ZoorqLens state="error" />)).toContain('data-motion="static"');
  });
});
