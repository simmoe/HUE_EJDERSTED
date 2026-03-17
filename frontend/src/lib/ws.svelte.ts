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

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return;
    const url = `ws://${window.location.host}/ws`;
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this.connected = true;
      if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    };

    this.ws.onmessage = (e: MessageEvent) => {
      const msg: ServerMsg = JSON.parse(e.data);
      this._handle(msg);
    };

    this.ws.onclose = () => {
      this.connected = false;
      this.reconnectTimer = setTimeout(() => this.connect(), 3000);
    };

    this.ws.onerror = () => this.ws?.close();
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
