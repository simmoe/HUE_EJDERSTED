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

export interface HueRoom {
  id: string;
  name: string;
  brightness: number;
  on: boolean;
  any_on: boolean;
  lights: number;
}

export interface HueStatus {
  ip: string | null;
  paired: boolean;
}

export interface NowPlaying {
  name: string;
  artist: string;
  album: string;
}

type ServerMsg =
  | { type: 'init'; devices: Device[]; volumes: Record<string, VolumeState>; hue_status: HueStatus; hue_rooms: HueRoom[]; now_playing: Record<string, NowPlaying> }
  | { type: 'device_added'; device: Device }
  | { type: 'device_removed'; device_id: string }
  | { type: 'volume_update'; device_id: string; level: number; online: boolean }
  | { type: 'hue_status'; ip: string | null; paired: boolean }
  | { type: 'hue_rooms'; rooms: HueRoom[] }
  | { type: 'now_playing'; device_id: string; name: string; artist: string; album: string }
  | { type: 'error'; device_id: string; message: string };

class WSStore {
  devices = $state<Device[]>([]);
  volumes = $state<Record<string, VolumeState>>({});
  hueStatus = $state<HueStatus>({ ip: null, paired: false });
  hueRooms = $state<HueRoom[]>([]);
  nowPlaying = $state<Record<string, NowPlaying>>({});
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
  private readonly RELOAD_AFTER_DOWN_MS = 120_000; // WS down >2 min → reload page

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
      // Hard reload if WS has been down too long (backend restart, cert rotation, hung renderer, ...)
      if (!this.connected && this.downSince && now - this.downSince > this.RELOAD_AFTER_DOWN_MS) {
        console.warn('[WS] down > 2 min — reloading page');
        window.location.reload();
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
        this.nowPlaying = msg.now_playing ?? {};
        break;
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
      case 'now_playing': {
        const np = { name: msg.name, artist: msg.artist, album: msg.album };
        if (np.name) {
          this.nowPlaying = { ...this.nowPlaying, [msg.device_id]: np };
        } else {
          const copy = { ...this.nowPlaying };
          delete copy[msg.device_id];
          this.nowPlaying = copy;
        }
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
}

export const store = new WSStore();
