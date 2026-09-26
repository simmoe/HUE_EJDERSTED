import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { pickWideBackDeviceId } from './cameraWide.ts';

const a12 = [
  { kind: 'videoinput', deviceId: 'front', label: 'camera2 1, facing front' },
  { kind: 'videoinput', deviceId: 'main', label: 'camera2 0, facing back' },
  { kind: 'videoinput', deviceId: 'uw', label: 'camera2 2, facing back' },
  { kind: 'videoinput', deviceId: 'depth', label: 'camera2 3, facing back' },
  { kind: 'videoinput', deviceId: 'macro', label: 'camera2 4, facing back' },
];

describe('pickWideBackDeviceId', () => {
  it('picks Samsung camera2 2 on an A12-style list', () => {
    assert.equal(pickWideBackDeviceId(a12), 'uw');
  });

  it('prefers an ultra-wide label over the Samsung slot', () => {
    assert.equal(
      pickWideBackDeviceId([
        ...a12,
        { kind: 'videoinput', deviceId: 'named', label: 'Ultra-wide camera, facing back' },
      ]),
      'named',
    );
  });

  it('skips front and unnamed lists', () => {
    assert.equal(pickWideBackDeviceId(a12.filter((d) => d.deviceId === 'front')), '');
    assert.equal(
      pickWideBackDeviceId([{ kind: 'videoinput', deviceId: 'x', label: '' }]),
      '',
    );
  });
});
