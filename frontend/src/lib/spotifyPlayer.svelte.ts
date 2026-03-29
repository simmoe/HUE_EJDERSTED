/**
 * Spotify Web Playback SDK — connects Chrome as a Spotify Connect device.
 * Exports reactive device_id for use in voice commands.
 */

let deviceId = $state<string | null>(null);
let ready = $state(false);
let playerInstance: any = null;

export function getDeviceId() { return deviceId; }
export function isReady() { return ready; }

export async function init() {
  if (playerInstance) return;

  // Fetch access token from backend
  const tokenRes = await fetch('/api/spotify/token');
  if (!tokenRes.ok) return;
  const { token } = await tokenRes.json();

  // Wait for SDK to load
  await new Promise<void>((resolve) => {
    if ((window as any).Spotify) { resolve(); return; }
    (window as any).onSpotifyWebPlaybackSDKReady = resolve;
  });

  const player = new (window as any).Spotify.Player({
    name: 'Ejdersted',
    getOAuthToken: async (cb: (t: string) => void) => {
      // Always fetch fresh token
      try {
        const r = await fetch('/api/spotify/token');
        if (r.ok) {
          const d = await r.json();
          cb(d.token);
          return;
        }
      } catch {}
      cb(token);
    },
    volume: 0.5,
  });

  player.addListener('ready', ({ device_id }: { device_id: string }) => {
    console.log('[Spotify SDK] Ready, device_id:', device_id);
    deviceId = device_id;
    ready = true;
  });

  player.addListener('not_ready', () => {
    console.log('[Spotify SDK] Not ready');
    ready = false;
  });

  player.addListener('initialization_error', ({ message }: any) => {
    console.error('[Spotify SDK] Init error:', message);
  });

  player.addListener('authentication_error', ({ message }: any) => {
    console.error('[Spotify SDK] Auth error:', message);
  });

  player.addListener('account_error', ({ message }: any) => {
    console.error('[Spotify SDK] Account error:', message);
  });

  await player.connect();
  playerInstance = player;
}
