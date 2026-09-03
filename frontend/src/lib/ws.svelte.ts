export interface Device {
  id: string;
  name: string;
  ip: string;
  auto_discovered: boolean;
}

export interface VolumeState {
  level: number;
  online: boolean;
}

export interface HueStatus {
  ip: string | null;
  paired: boolean;
}

export interface HueRoom {
  id: string;
  name: string;
  brightness: number;
  on: boolean;
  any_on: boolean;
  lights: number;
}

export interface GardenLight {
  id: string;
  name: string;
  brightness: number;
  on: boolean;
  any_on: boolean;
  online: boolean;
  lights: number;
  error?: string;
  has_color?: boolean;
  mode?: string;
  hue?: number;
  sat?: number;
  hex?: string;
}

export interface NowPlaying {
  name: string;
  artist: string;
  album: string;
  playing?: boolean;
}

export interface AudioTargetSummary {
  id: string;
  name: string;
  type: string;
  default: boolean;
}

export interface AudioTargetStatus extends AudioTargetSummary {
  online: boolean;
  paired: boolean;
  trusted: boolean;
  connected: boolean;
  playback: boolean;
  volume?: number;
  error?: string;
}

export type SolarMode = 'auto' | 'on' | 'off';

export interface SolarStatus {
  enabled: boolean;
  relayOn?: boolean;
  mode?: SolarMode;
  onTime?: string | null;
  offTime?: string | null;
  sunrise?: string | null;
  sunset?: string | null;
  withinWindow?: boolean;
  clockTrusted?: boolean;
  simulated?: boolean;
  now?: string;
}

export interface SwitchbotStatus {
  enabled: boolean;
  simulated?: boolean;
  name?: string;
  mac?: string | null;
  macShort?: string | null;
  ready?: boolean;
  pressing?: boolean;
  lastPressAt?: string | null;
  lastError?: string | null;
}

export interface HubConfig {
  site: 'home' | 'garden' | string;
  publicUrl: string;
  features: {
    camera: boolean;
    audio: boolean;
    hue: boolean;
    spotify: boolean;
    podcasts: boolean;
    playlists: boolean;
    adbKiosk: boolean;
    solar: boolean;
    lights?: boolean;
    switchbot?: boolean;
  };
  camera: {
    mode: 'publisher' | 'viewer';
  };
  audio?: {
    defaultTarget: string;
    targets: AudioTargetSummary[];
  };
}

export const defaultHubConfig: HubConfig = {
  site: 'home',
  publicUrl: '',
  features: {
    camera: true,
    audio: true,
    hue: true,
    spotify: true,
    podcasts: true,
    playlists: true,
    adbKiosk: true,
    solar: false,
    lights: false,
    switchbot: false,
  },
  camera: {
    mode: 'viewer',
  },
  audio: {
    defaultTarget: '',
    targets: [],
  },
};

type ServerMsg =
  | { type: 'init'; devices: Device[]; volumes: Record<string, VolumeState>; hue_status: HueStatus; hue_rooms: HueRoom[]; lights?: GardenLight[]; now_playing: Record<string, NowPlaying>; config?: HubConfig; solar?: SolarStatus; switchbot?: SwitchbotStatus }
  | ({ type: 'solar_status' } & SolarStatus)
  | ({ type: 'switchbot_status' } & SwitchbotStatus)
  | { type: 'device_added'; device: Device }
  | { type: 'device_removed'; device_id: string }
  | { type: 'volume_update'; device_id: string; level: number; online: boolean }
  | { type: 'hue_status'; ip: string | null; paired: boolean }
  | { type: 'hue_rooms'; rooms: HueRoom[] }
  | { type: 'lights'; lights: GardenLight[] }
  | { type: 'now_playing'; device_id: string; name: string; artist: string; album: string; playing?: boolean }
  | { type: 'error'; device_id: string; message: string };

export function hsvToHex(hue: number, sat: number, value = 100): string {
  const h = ((hue % 360) + 360) % 360 / 360;
  const s = Math.max(0, Math.min(100, sat)) / 100;
  const v = Math.max(0, Math.min(100, value)) / 100;
  const i = Math.floor(h * 6);
  const f = h * 6 - i;
  const p = v * (1 - s);
  const q = v * (1 - f * s);
  const t = v * (1 - (1 - f) * s);
  const [r, g, b] = (
    [
      [v, t, p],
      [q, v, p],
      [p, v, t],
      [p, q, v],
      [t, p, v],
      [v, p, q],
    ][i % 6]
  );
  const hex = (n: number) => Math.round(n * 255).toString(16).padStart(2, '0');
  return `#${hex(r)}${hex(g)}${hex(b)}`;
}

class WSStore {
  devices = $state<Device[]>([]);
  volumes = $state<Record<string, VolumeState>>({});
  hueStatus = $state<HueStatus>({ ip: null, paired: false });
  hueRooms = $state<HueRoom[]>([]);
  lights = $state<GardenLight[]>([]);
  nowPlaying = $state<Record<string, NowPlaying>>({});
  config = $state<HubConfig>(defaultHubConfig);
  solar = $state<SolarStatus>({ enabled: false });
  switchbot = $state<SwitchbotStatus>({ enabled: false });
  connected = $state(false);

  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pending = new Map<string, ReturnType<typeof setTimeout>>();

  // Heartbeat/watchdog
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private staleTimer: ReturnType<typeof setInterval> | null = null;
  private lastMessageAt = 0;
  private downSince = 0;
  private watchdogStarted = false;
  private reconnectAttempts = 0;

  // Tunables
  private readonly HEARTBEAT_MS = 20_000;         // send ping every 20s
  private readonly STALE_MS = 45_000;             // no message in 45s → force reconnect
  private readonly RECONNECT_NUDGE_AFTER_DOWN_MS = 120_000;

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN || this.ws?.readyState === WebSocket.CONNECTING) return;
    if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${window.location.host}/ws`;
    console.log('[WS] connecting to', url, `(attempt ${this.reconnectAttempts + 1})`);
    this.reconnectAttempts += 1;

    try {
      this.ws = new WebSocket(url);
    } catch (err) {
      console.error('[WS] construct failed', err);
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      console.log('[WS] connected');
      this.connected = true;
      this.lastMessageAt = Date.now();
      this.downSince = 0;
      this.reconnectAttempts = 0;
      this._startHeartbeat();
      this._ensureWatchdog();
    };

    this.ws.onmessage = (e: MessageEvent) => {
      this.lastMessageAt = Date.now();
      let msg: ServerMsg | { type: 'pong' };
      try { msg = JSON.parse(e.data); } catch { return; }
      if (msg.type === 'pong') return;
      console.log('[WS] ←', msg.type, msg.type === 'init' ? `(${msg.hue_rooms?.length} rooms, paired=${msg.hue_status?.paired})` : '');
      this._handle(msg as ServerMsg);
    };

    this.ws.onclose = (e) => {
      console.log('[WS] closed', e.code, e.reason);
      this.connected = false;
      if (!this.downSince) this.downSince = Date.now();
      this._stopHeartbeat();
      this._scheduleReconnect();
    };

    this.ws.onerror = (e) => { console.error('[WS] error', e); try { this.ws?.close(); } catch { /* */ } };
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer) return;
    // Backoff: 500ms, 1s, 2s, 3s (cap)
    const delay = Math.min(500 * Math.max(1, this.reconnectAttempts), 3000);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private _startHeartbeat() {
    this._stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        try { this.ws.send(JSON.stringify({ type: 'ping', t: Date.now() })); } catch { /* */ }
      }
    }, this.HEARTBEAT_MS);
  }

  private _stopHeartbeat() {
    if (this.heartbeatTimer) { clearInterval(this.heartbeatTimer); this.heartbeatTimer = null; }
  }

  private _ensureWatchdog() {
    if (this.watchdogStarted) return;
    this.watchdogStarted = true;

    this.staleTimer = setInterval(() => {
      const now = Date.now();
      // Force close if we haven't heard anything in STALE_MS (dead socket)
      if (this.ws?.readyState === WebSocket.OPEN && this.lastMessageAt && now - this.lastMessageAt > this.STALE_MS) {
        console.warn('[WS] stale — forcing reconnect');
        try { this.ws.close(); } catch { /* */ }
      }
      // Keep reconnecting in-place. A hard reload is too visible on the kiosk and
      // looks like Chrome restarted when Android briefly suspends networking.
      if (!this.connected && this.downSince && now - this.downSince > this.RECONNECT_NUDGE_AFTER_DOWN_MS) {
        console.warn('[WS] down > 2 min — retrying without page reload');
        if (!this.reconnectTimer) this._scheduleReconnect();
      }
    }, 5_000);

    const wake = (reason: string) => {
      console.log('[WS] wake:', reason);
      if (this.ws?.readyState !== WebSocket.OPEN) {
        if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
        this.connect();
      } else {
        // Nudge: send a ping immediately to verify the path actually works
        try { this.ws.send(JSON.stringify({ type: 'ping', t: Date.now() })); } catch { /* */ }
      }
    };

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') wake('visibilitychange');
    });
    window.addEventListener('pageshow', () => wake('pageshow'));
    window.addEventListener('online', () => wake('online'));
    window.addEventListener('focus', () => wake('focus'));
  }

  private _handle(msg: ServerMsg) {
    switch (msg.type) {
      case 'init':
        this.devices = msg.devices;
        this.volumes = msg.volumes;
        this.hueStatus = msg.hue_status;
        this.hueRooms = msg.hue_rooms;
        this.lights = msg.lights ?? [];
        this.nowPlaying = msg.now_playing ?? {};
        this.config = msg.config ?? defaultHubConfig;
        if (msg.solar) this.solar = msg.solar;
        if (msg.switchbot) this.switchbot = msg.switchbot;
        break;
      case 'solar_status': {
        const { type, ...rest } = msg;
        this.solar = rest as SolarStatus;
        break;
      }
      case 'switchbot_status': {
        const { type, ...rest } = msg;
        this.switchbot = rest as SwitchbotStatus;
        break;
      }
      case 'device_added':
        this.devices = [
          ...this.devices.filter((d) => d.id !== msg.device.id),
          msg.device,
        ];
        break;
      case 'device_removed': {
        this.devices = this.devices.filter((d) => d.id !== msg.device_id);
        const vols = { ...this.volumes };
        delete vols[msg.device_id];
        this.volumes = vols;
        break;
      }
      case 'volume_update':
        this.volumes = {
          ...this.volumes,
          [msg.device_id]: { level: msg.level, online: msg.online },
        };
        break;
      case 'hue_status':
        this.hueStatus = { ip: msg.ip, paired: msg.paired };
        break;
      case 'hue_rooms':
        this.hueRooms = msg.rooms;
        break;
      case 'lights':
        this.lights = msg.lights ?? [];
        break;
      case 'now_playing': {
        if (!msg.name) {
          const copy = { ...this.nowPlaying };
          delete copy[msg.device_id];
          this.nowPlaying = copy;
          break;
        }
        const prev = this.nowPlaying[msg.device_id];
        this.nowPlaying = {
          ...this.nowPlaying,
          [msg.device_id]: {
            name: msg.name,
            artist: msg.artist,
            album: msg.album,
            playing: typeof msg.playing === 'boolean' ? msg.playing : prev?.playing,
          },
        };
        break;
      }
    }
  }

  setVolume(deviceId: string, level: number) {
    // Optimistic update
    this.volumes = {
      ...this.volumes,
      [deviceId]: { ...this.volumes[deviceId], level },
    };
    // Debounced send (80 ms)
    const existing = this.pending.get(deviceId);
    if (existing) clearTimeout(existing);
    this.pending.set(
      deviceId,
      setTimeout(() => {
        this.ws?.send(JSON.stringify({ type: 'set_volume', device_id: deviceId, level }));
        this.pending.delete(deviceId);
      }, 80)
    );
  }

  setSolarMode(mode: SolarMode) {
    // Optimistic: reflect the chosen mode immediately; backend confirms via solar_status.
    this.solar = { ...this.solar, mode };
    this.ws?.send(JSON.stringify({ type: 'set_solar_mode', mode }));
  }

  async pressSwitchbot(): Promise<string | null> {
    this.switchbot = { ...this.switchbot, pressing: true, lastError: null };
    const r = await fetch('/api/switchbot/press', { method: 'POST' });
    const data = await r.json();
    this.switchbot = {
      enabled: true,
      ...data,
      pressing: false,
    };
    return data.ok ? null : (data.error ?? 'kunne ikke trykke');
  }

  setHueBrightness(roomId: string, brightness: number) {
    // Optimistic update
    this.hueRooms = this.hueRooms.map((r) =>
      r.id === roomId ? { ...r, brightness, on: brightness > 0 } : r
    );
    const key = `hue_${roomId}`;
    const existing = this.pending.get(key);
    if (existing) clearTimeout(existing);
    this.pending.set(
      key,
      setTimeout(() => {
        this.ws?.send(JSON.stringify({ type: 'set_hue_brightness', room_id: roomId, brightness }));
        this.pending.delete(key);
      }, 80)
    );
  }

  setLightBrightness(lightId: string, brightness: number) {
    this.lights = this.lights.map((l) =>
      l.id === lightId ? { ...l, brightness, on: brightness > 0, any_on: brightness > 0 } : l
    );
    const key = `light_${lightId}`;
    const existing = this.pending.get(key);
    if (existing) clearTimeout(existing);
    this.pending.set(
      key,
      setTimeout(() => {
        this.ws?.send(JSON.stringify({ type: 'set_light_brightness', light_id: lightId, brightness }));
        this.pending.delete(key);
      }, 80)
    );
  }

  setLightColor(lightId: string, hue: number, sat: number) {
    const hex = hsvToHex(hue, sat);
    this.lights = this.lights.map((l) =>
      l.id === lightId ? { ...l, hue, sat, hex, mode: 'colour', on: true, any_on: true } : l
    );
    const key = `light_color_${lightId}`;
    const existing = this.pending.get(key);
    if (existing) clearTimeout(existing);
    this.pending.set(
      key,
      setTimeout(() => {
        this.ws?.send(JSON.stringify({ type: 'set_light_color', light_id: lightId, hue, sat }));
        this.pending.delete(key);
      }, 80)
    );
  }

  setLightWhite(lightId: string) {
    this.lights = this.lights.map((l) =>
      l.id === lightId ? { ...l, mode: 'white', on: true, any_on: true } : l
    );
    this.ws?.send(JSON.stringify({ type: 'set_light_white', light_id: lightId }));
  }

  async connectLight(lightId: string): Promise<GardenLight | null> {
    const r = await fetch('/api/lights/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ light_id: lightId }),
    });
    const data = await r.json();
    if (data.ok && Array.isArray(data.lights)) {
      this.lights = data.lights;
    }
    return data.light ?? null;
  }

  async pairHue(ip?: string): Promise<string | null> {
    const r = await fetch('/api/hue/pair', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(ip ? { ip } : {}),
    });
    const data = await r.json();
    return data.ok ? null : (data.error ?? 'Fejl');
  }

  async addDevice(ip: string, name: string): Promise<string | null> {
    const r = await fetch('/api/devices', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ip, name }),
    });
    const data = await r.json();
    return data.error ?? null;
  }

  async removeDevice(deviceId: string) {
    await fetch(`/api/devices/${deviceId}`, { method: 'DELETE' });
  }

  async getAudioTargets(): Promise<AudioTargetStatus[]> {
    const r = await fetch('/api/audio/targets');
    if (!r.ok) return [];
    return (await r.json()) as AudioTargetStatus[];
  }

  async connectAudioTarget(targetId: string): Promise<AudioTargetStatus> {
    const r = await fetch(`/api/audio/targets/${encodeURIComponent(targetId)}/connect`, { method: 'POST' });
    return (await r.json()) as AudioTargetStatus;
  }

  async setAudioTargetVolume(targetId: string, level: number): Promise<{ ok: boolean; volume?: number; error?: string }> {
    const r = await fetch(`/api/audio/targets/${encodeURIComponent(targetId)}/volume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level }),
    });
    return (await r.json()) as { ok: boolean; volume?: number; error?: string };
  }
}

export const store = new WSStore();
