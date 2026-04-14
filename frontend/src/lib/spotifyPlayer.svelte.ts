/**
 * Spotify Web Playback SDK — Chrome bliver Connect-enhed («Ejdersted»),
 * så hubbens play-uris altid har et device_id (Mac dev + Android kiosk).
 * Script: app.html — sdk.scdn.co/spotify-player.js
 */
let deviceId = $state<string | null>(null);
let ready = $state(false);
let playerInstance: any = null;
let initPromise: Promise<void> | null = null;

export type PlaybackState = {
  trackUri: string | null;
  paused: boolean;
  position: number;
  duration: number;
};

type StateListener = (s: PlaybackState) => void;
const stateListeners: Set<StateListener> = new Set();

export function onStateChange(fn: StateListener): () => void {
  stateListeners.add(fn);
  return () => stateListeners.delete(fn);
}

function emitState(raw: any) {
  if (!raw) {
    const s: PlaybackState = { trackUri: null, paused: true, position: 0, duration: 0 };
    for (const fn of stateListeners) fn(s);
    return;
  }
  const cur = raw.track_window?.current_track;
  const s: PlaybackState = {
    trackUri: cur?.uri ?? null,
    paused: raw.paused ?? true,
    position: raw.position ?? 0,
    duration: raw.duration ?? 0,
  };
  for (const fn of stateListeners) fn(s);
}

export function getDeviceId(): string | null {
  return deviceId;
}

export function isReady(): boolean {
  return ready;
}

/** Vent på device_id efter init (ready-event efter connect). */
export function waitForWebDevice(maxMs = 15000): Promise<string | null> {
  if (deviceId) return Promise.resolve(deviceId);
  return new Promise((resolve) => {
    const t0 = Date.now();
    const tick = () => {
      if (deviceId) {
        resolve(deviceId);
        return;
      }
      if (Date.now() - t0 >= maxMs) {
        resolve(null);
        return;
      }
      setTimeout(tick, 120);
    };
    tick();
  });
}

function loadSdk(): Promise<void> {
  const w = window as any;
  if (w.Spotify?.Player) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 25_000;
    const tick = () => {
      if (w.Spotify?.Player) {
        resolve();
        return;
      }
      if (Date.now() > deadline) {
        reject(new Error('Spotify SDK timeout (25s) — tjek at sdk.scdn.co ikke blokeres'));
        return;
      }
      setTimeout(tick, 40);
    };
    tick();
  });
}

async function doInit(): Promise<void> {
  if (playerInstance) return;

  const tokenRes = await fetch('/api/spotify/token');
  if (!tokenRes.ok) {
    console.warn('[Spotify SDK] /api/spotify/token failed:', tokenRes.status);
    return;
  }
  const { token } = (await tokenRes.json()) as { token?: string };
  if (!token) {
    console.warn('[Spotify SDK] No token in response');
    return;
  }

  try {
    await loadSdk();
  } catch (e) {
    console.warn('[Spotify SDK]', e);
    return;
  }

  const SpotifyCtor = (window as any).Spotify;
  if (!SpotifyCtor?.Player) {
    console.warn('[Spotify SDK] window.Spotify.Player unavailable');
    return;
  }

  let settled = false;
  let readyResolve: (id: string) => void;
  let readyReject: (e: Error) => void;
  const readyPromise = new Promise<string>((res, rej) => {
    readyResolve = (id: string) => {
      if (settled) return;
      settled = true;
      res(id);
    };
    readyReject = (e: Error) => {
      if (settled) return;
      settled = true;
      rej(e);
    };
    setTimeout(() => readyReject(new Error('Player ready timeout (20s)')), 20_000);
  });

  const player = new SpotifyCtor.Player({
    name: 'Ejdersted',
    getOAuthToken: async (cb: (t: string) => void) => {
      try {
        const r = await fetch('/api/spotify/token');
        if (r.ok) {
          const d = (await r.json()) as { token?: string };
          if (d.token) {
            cb(d.token);
            return;
          }
        }
      } catch {
        /* */
      }
      cb(token);
    },
    volume: 1,
  });

  player.addListener('ready', ({ device_id }: { device_id: string }) => {
    deviceId = device_id;
    ready = true;
    console.log('[Spotify SDK] Ready', device_id);
    readyResolve(device_id);
  });

  player.addListener('not_ready', () => {
    ready = false;
    console.log('[Spotify SDK] not_ready');
  });

  player.addListener('initialization_error', ({ message }: { message: string }) => {
    console.error('[Spotify SDK] init error', message);
    readyReject(new Error(message));
  });

  player.addListener('authentication_error', ({ message }: { message: string }) => {
    console.error('[Spotify SDK] auth error', message);
    readyReject(new Error(message));
  });

  player.addListener('account_error', ({ message }: { message: string }) => {
    console.error('[Spotify SDK] account error', message);
    readyReject(new Error(message));
  });

  player.addListener('player_state_changed', (state: any) => {
    emitState(state);
  });

  const connected = await player.connect();
  if (!connected) {
    console.warn('[Spotify SDK] player.connect() returned false');
    return;
  }

  try {
    await readyPromise;
  } catch (e) {
    console.warn('[Spotify SDK]', e);
    try {
      await player.disconnect();
    } catch {
      /* */
    }
    return;
  }

  playerInstance = player;
}

export async function init(): Promise<void> {
  if (playerInstance) return;
  if (!initPromise) {
    initPromise = doInit().finally(() => {
      initPromise = null;
    });
  }
  await initPromise;
}
