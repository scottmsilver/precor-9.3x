import { describe, it, expect } from 'vitest';
import { apcaLc } from './apca';
import golden from '../../../docs/bg-lab/golden.json';

describe('apca', () => {
  it('matches canonical anchors', () => {
    expect(apcaLc('#000000', '#FFFFFF')).toBeCloseTo(106.04, 1);
    expect(apcaLc('#FFFFFF', '#000000')).toBeCloseTo(-107.88, 1);
  });
  it('matches every golden apca row', () => {
    for (const row of golden.apca) {
      expect(apcaLc(row.text, row.bg)).toBeCloseTo(row.lc, 1);
    }
  });
});
