import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { parseFacing } from './cameraFacing.ts';

describe('parseFacing', () => {
  it('keeps front and rear', () => {
    assert.equal(parseFacing('user'), 'user');
    assert.equal(parseFacing('environment'), 'environment');
  });

  it('rejects anything else', () => {
    assert.equal(parseFacing('bag'), '');
    assert.equal(parseFacing(null), '');
  });
});
