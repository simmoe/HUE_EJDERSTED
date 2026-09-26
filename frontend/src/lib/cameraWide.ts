export type MediaDeviceLike = {
  kind: string;
  label: string;
  deviceId: string;
};

const BACK = /facing back|\bback\b|environment/i;
const FRONT = /facing front|\bfront\b/;
const WIDE_LABEL = /ultra|uw\b|wide.?angle|vidvinkel|0\s*\.?\s*5\s*x/i;
const SKIP = /macro|depth|telephoto|\btele\b/;
const SAMSUNG_UW = /camera2\s+2,\s*facing back/i;
const SAMSUNG_MAIN = /camera2\s+0,\s*facing back/i;

function scoreWide(label: string): number {
  if (!label || FRONT.test(label) || SKIP.test(label)) return -100;
  if (WIDE_LABEL.test(label)) return 100;
  if (SAMSUNG_UW.test(label)) return 80;
  if (SAMSUNG_MAIN.test(label)) return -10;
  if (BACK.test(label)) return 0;
  return -100;
}

/** Galaxy A12 (og lign. Samsung): 115° sidder på camera2 2, ikke hovedlinsen. */
export function pickWideBackDeviceId(devices: MediaDeviceLike[]): string {
  let bestId = '';
  let best = 0;
  for (const device of devices) {
    if (device.kind !== 'videoinput' || !device.deviceId) continue;
    const score = scoreWide(device.label);
    if (score > best) {
      best = score;
      bestId = device.deviceId;
    }
  }
  return best > 0 ? bestId : '';
}

type ZoomCap = { zoom?: { min?: number } };

export async function applyWidestZoom(track: MediaStreamTrack): Promise<boolean> {
  const caps = (typeof track.getCapabilities === 'function' ? track.getCapabilities() : {}) as ZoomCap;
  const min = caps.zoom?.min;
  if (typeof min !== 'number' || min >= 1) return false;
  try {
    await track.applyConstraints({ advanced: [{ zoom: min }] });
    return true;
  } catch {
    return false;
  }
}
