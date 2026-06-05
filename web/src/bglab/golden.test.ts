import { describe, it, expect } from 'vitest';
import golden from '../../../docs/bg-lab/golden.json';
import { chooseTheme } from './engine';

describe('golden themes', () => {
  for (const c of (golden as any).themes) {
    it(`reproduces theme: ${c.name}`, () => {
      const t = chooseTheme(c.regions, c.prior);
      expect(t.theme).toEqual(c.theme);
    });
  }
});
