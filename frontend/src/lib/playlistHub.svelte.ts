/**
 * Delte playlister på tværs af browsere via Firestore.
 * Konfiguration hentes fra hub: GET /api/config/firebase (hub_globals.json).
 *
 * Firestore: dokument `ejdersted/playlists`. Sæt regler i konsollen (fx kun hjemmenet)
 * — uden auth skal read/write være bevidst åbne eller låst til dit netværk.
 *
 * Tilstand ligger i ét `playlist`-objekt (Svelte 5: eksporteret $state må ikke reassignedes som enkeltfelter).
 */
import { initializeApp, type FirebaseOptions } from 'firebase/app';
import {
  getFirestore,
  doc,
  onSnapshot,
  setDoc,
  serverTimestamp,
  type Unsubscribe,
  type DocumentReference,
} from 'firebase/firestore';
import { init as initSpotifyWebPlayer } from '$lib/spotifyPlayer.svelte';

export type QTrack = { uri: string; name: string; artist: string };

export const playlist = $state({
  spotifyTitle: '',
  spotifyArtist: '',
  spotifyNextTitle: '',
  spotifyNextArtist: '',
  spotifyTrackUri: '',
  spotifyPlaying: false,
  micQueue: [] as QTrack[],
  radioQueue: [] as QTrack[],
  albumQueue: [] as QTrack[],
  micIndex: 0,
  radioIndex: 0,
  albumIndex: 0,
  playListMode: 'mic' as 'mic' | 'radio' | 'album',
  spotifyRadio: false,
  spotifyRadioLoading: false,
  spotifyRadioError: '',
  spotifyAlbumActive: false,
  spotifyAlbumLoading: false,
  spotifyAlbumError: '',
});

let scrollToNowPlayingImpl: (() => void) | undefined;
export function registerScrollToNowPlaying(fn: () => void) {
  scrollToNowPlayingImpl = fn;
}

function scrollToNowPlaying() {
  scrollToNowPlayingImpl?.();
}

let docRef: DocumentReference | null = null;
let unsub: Unsubscribe | null = null;
let pushTimer: ReturnType<typeof setTimeout> | null = null;
let applyingRemote = false;

const ADVANCE_BUFFER_MS = 300;
let advanceTimer: ReturnType<typeof setTimeout> | null = null;
let pausedRemainingMs = 0;

function clearAdvanceTimer() {
  if (advanceTimer) {
    clearTimeout(advanceTimer);
    advanceTimer = null;
  }
}

function scheduleAdvance(ms: number) {
  clearAdvanceTimer();
  pausedRemainingMs = 0;
  const startedAt = Date.now();
  advanceTimer = setTimeout(() => {
    advanceTimer = null;
    const q = activeQueue();
    const idx = activeIndex();
    if (idx + 1 < q.length) {
      setActiveIndex(idx + 1);
      paintNpFromQueues();
      scrollToNowPlaying();
      void playFromCurrentIndex();
    } else {
      playlist.spotifyPlaying = false;
      schedulePush();
    }
  }, ms + ADVANCE_BUFFER_MS);
  (scheduleAdvance as any)._startedAt = startedAt;
  (scheduleAdvance as any)._totalMs = ms + ADVANCE_BUFFER_MS;
}

function pauseAdvanceTimer() {
  if (!advanceTimer) return;
  const startedAt = (scheduleAdvance as any)._startedAt ?? 0;
  const totalMs = (scheduleAdvance as any)._totalMs ?? 0;
  const elapsed = Date.now() - startedAt;
  pausedRemainingMs = Math.max(0, totalMs - elapsed);
  clearAdvanceTimer();
}

function resumeAdvanceTimer() {
  if (pausedRemainingMs > 0) {
    scheduleAdvance(pausedRemainingMs - ADVANCE_BUFFER_MS);
  }
}

function isQTrack(x: unknown): x is QTrack {
  return (
    typeof x === 'object' &&
    x !== null &&
    'uri' in x &&
    typeof (x as { uri: unknown }).uri === 'string'
  );
}

function parseQueue(a: unknown): QTrack[] {
  if (!Array.isArray(a)) return [];
  return a
    .filter(isQTrack)
    .map((t) => ({ uri: t.uri, name: String(t.name ?? ''), artist: String(t.artist ?? '') }));
}

function parseMode(x: unknown): 'mic' | 'radio' | 'album' {
  if (x === 'radio' || x === 'album' || x === 'mic') return x;
  return 'mic';
}

function serializeSyncPayload(): string {
  const p = playlist;
  return JSON.stringify({
    micQueue: p.micQueue,
    radioQueue: p.radioQueue,
    albumQueue: p.albumQueue,
    micIndex: p.micIndex,
    radioIndex: p.radioIndex,
    albumIndex: p.albumIndex,
    playListMode: p.playListMode,
    spotifyRadio: p.spotifyRadio,
    spotifyAlbumActive: p.spotifyAlbumActive,
    spotifyPlaying: p.spotifyPlaying,
  });
}

function schedulePush() {
  if (applyingRemote || !docRef) return;
  if (pushTimer) clearTimeout(pushTimer);
  pushTimer = setTimeout(() => {
    pushTimer = null;
    void flushPush();
  }, 400);
}

async function flushPush() {
  if (!docRef || applyingRemote) return;
  const p = playlist;
  const payload = {
    micQueue: p.micQueue,
    radioQueue: p.radioQueue,
    albumQueue: p.albumQueue,
    micIndex: p.micIndex,
    radioIndex: p.radioIndex,
    albumIndex: p.albumIndex,
    playListMode: p.playListMode,
    spotifyRadio: p.spotifyRadio,
    spotifyAlbumActive: p.spotifyAlbumActive,
    spotifyPlaying: p.spotifyPlaying,
    updatedAt: serverTimestamp(),
  };
  try {
    await setDoc(docRef, payload, { merge: true });
  } catch {
    /* hub offline eller regler */
  }
}

export function activeQueue(): QTrack[] {
  if (playlist.playListMode === 'radio') return playlist.radioQueue;
  if (playlist.playListMode === 'album') return playlist.albumQueue;
  return playlist.micQueue;
}

export function activeIndex(): number {
  if (playlist.playListMode === 'radio') return playlist.radioIndex;
  if (playlist.playListMode === 'album') return playlist.albumIndex;
  return playlist.micIndex;
}

function setActiveIndex(i: number) {
  const q = activeQueue();
  const n = Math.max(0, Math.min(q.length - 1, i));
  if (playlist.playListMode === 'radio') playlist.radioIndex = n;
  else if (playlist.playListMode === 'album') playlist.albumIndex = n;
  else playlist.micIndex = n;
}

export function paintNpFromQueues() {
  const q = activeQueue();
  const idx = activeIndex();
  const row = q[idx];
  if (!row) {
    playlist.spotifyTitle = '';
    playlist.spotifyArtist = '';
    playlist.spotifyTrackUri = '';
    playlist.spotifyNextTitle = '';
    playlist.spotifyNextArtist = '';
    if (!applyingRemote) schedulePush();
    return;
  }
  playlist.spotifyTitle = row.name;
  playlist.spotifyArtist = row.artist;
  playlist.spotifyTrackUri = row.uri;
  const nxt = q[idx + 1];
  playlist.spotifyNextTitle = nxt?.name ?? '';
  playlist.spotifyNextArtist = nxt?.artist ?? '';
  if (!applyingRemote) schedulePush();
}

/** Det spor NP-kortet viser lige nu (uanset mic / radio / album-mode). */
function seedTrackFromDisplayedNp(): QTrack | undefined {
  const q = activeQueue();
  const idx = activeIndex();
  const row = q[idx];
  if (!row?.uri?.startsWith('spotify:track:')) return undefined;
  return { uri: row.uri, name: row.name, artist: row.artist };
}

function seedUriForAlbumBuild(): string {
  const q = activeQueue();
  const idx = activeIndex();
  return q[idx]?.uri ?? '';
}

async function playFromCurrentIndex(): Promise<boolean> {
  const q = activeQueue();
  const idx = activeIndex();
  const uri = q[idx]?.uri;
  if (!uri?.startsWith('spotify:track:')) return false;
  try {
    await initSpotifyWebPlayer();
    const r = await fetch('/api/spotify/play-uris', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uris: [uri], offset: 0, position_ms: 0 }),
    });
    const data = (await r.json()) as { ok: boolean; duration_ms?: number };
    if (data.ok) {
      playlist.spotifyPlaying = true;
      if (data.duration_ms && data.duration_ms > 0) {
        scheduleAdvance(data.duration_ms);
      }
      schedulePush();
      return true;
    }
  } catch {
    /* */
  }
  return false;
}

export async function togglePlayPause() {
  if (playlist.spotifyPlaying) {
    try {
      const r = await fetch('/api/spotify/pause', { method: 'POST' });
      const data = await r.json();
      if (data.ok) {
        playlist.spotifyPlaying = false;
        pauseAdvanceTimer();
      }
    } catch {
      /* */
    }
    schedulePush();
    return;
  }
  if (pausedRemainingMs > 0) {
    const ok = await resumePlayback();
    if (ok) {
      playlist.spotifyPlaying = true;
      resumeAdvanceTimer();
      schedulePush();
      return;
    }
  }
  await playFromCurrentIndex();
}

async function resumePlayback(): Promise<boolean> {
  try {
    const r = await fetch('/api/spotify/resume', { method: 'POST' });
    const data = await r.json();
    return !!data.ok;
  } catch {
    return false;
  }
}

async function pausePlaybackNow() {
  try {
    await fetch('/api/spotify/pause', { method: 'POST' });
  } catch {
    /* */
  }
  playlist.spotifyPlaying = false;
  clearAdvanceTimer();
  pausedRemainingMs = 0;
}

export async function spotifyNextTrack() {
  const q = activeQueue();
  if (q.length <= 1) return;
  await pausePlaybackNow();
  setActiveIndex(activeIndex() + 1);
  paintNpFromQueues();
}

export async function spotifyPreviousTrack() {
  const q = activeQueue();
  if (q.length <= 1) return;
  await pausePlaybackNow();
  setActiveIndex(activeIndex() - 1);
  paintNpFromQueues();
}

export async function toggleRadio() {
  if (playlist.spotifyRadio) {
    let current: QTrack | undefined;
    if (playlist.radioQueue.length) {
      const i = Math.min(playlist.radioIndex, playlist.radioQueue.length - 1);
      const r = playlist.radioQueue[i];
      if (r?.uri?.startsWith('spotify:track:'))
        current = { uri: r.uri, name: r.name, artist: r.artist };
    }
    try {
      await fetch('/api/spotify/radio', { method: 'DELETE' });
    } catch {
      /* */
    }
    playlist.spotifyRadio = false;
    playlist.spotifyRadioError = '';
    playlist.playListMode = 'mic';
    if (current) {
      playlist.micQueue = [...playlist.micQueue, current];
      playlist.micIndex = playlist.micQueue.length - 1;
    }
    paintNpFromQueues();
    return;
  }
  const seed = seedTrackFromDisplayedNp();
  if (!seed?.uri) {
    playlist.spotifyRadioError = 'Ingen sang på afspilleren — vælg spor med forrige/næste eller tilføj til køen';
    return;
  }
  playlist.spotifyRadioLoading = true;
  playlist.spotifyRadioError = '';
  playlist.spotifyAlbumActive = false;
  playlist.playListMode = 'mic';
  scrollToNowPlaying();
  try {
    const r = await fetch('/api/spotify/radio/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        seed_uri: seed.uri,
        seed_name: seed.name,
        seed_artist: seed.artist,
      }),
    });
    const data = await r.json();
    if (!data.ok) {
      playlist.spotifyRadio = false;
      playlist.spotifyRadioError = (data.error as string) ?? 'Radio fejlede';
      return;
    }
    playlist.radioQueue = (data.queue as QTrack[]) ?? [];
    playlist.radioIndex = 0;
    playlist.spotifyRadio = true;
    playlist.playListMode = 'radio';
    playlist.spotifyRadioError = '';
    paintNpFromQueues();
  } catch {
    playlist.spotifyRadio = false;
    playlist.spotifyRadioError = 'Ingen forbindelse til hub';
  } finally {
    playlist.spotifyRadioLoading = false;
  }
}

export async function playAlbum() {
  if (playlist.spotifyAlbumActive) {
    playlist.spotifyAlbumActive = false;
    playlist.spotifyAlbumError = '';
    playlist.playListMode = 'mic';
    paintNpFromQueues();
    return;
  }
  playlist.spotifyAlbumError = '';
  const uri = seedUriForAlbumBuild();
  if (!uri) {
    playlist.spotifyAlbumError = 'Vælg et track i køen';
    return;
  }
  await pausePlaybackNow();
  playlist.spotifyAlbumLoading = true;
  playlist.spotifyRadio = false;
  scrollToNowPlaying();
  try {
    const r = await fetch('/api/spotify/album/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_uri: uri }),
    });
    const data = await r.json();
    if (!data.ok) {
      playlist.spotifyAlbumActive = false;
      playlist.spotifyAlbumError = (data.error as string) ?? 'Album fejlede';
      return;
    }
    playlist.albumQueue = (data.queue as QTrack[]) ?? [];
    playlist.albumIndex = 0;
    playlist.spotifyAlbumActive = true;
    playlist.playListMode = 'album';
    paintNpFromQueues();
  } catch {
    playlist.spotifyAlbumActive = false;
    playlist.spotifyAlbumError = 'Ingen forbindelse til hub';
  } finally {
    playlist.spotifyAlbumLoading = false;
  }
}

export function handleVoicePayload(data: Record<string, unknown>) {
  if (data.action === 'enqueue' && data.ok && data.uri) {
    const row: QTrack = {
      uri: String(data.uri),
      name: String(data.name ?? ''),
      artist: String(data.artist ?? ''),
    };
    playlist.micQueue = [...playlist.micQueue, row];
    playlist.playListMode = 'mic';
    playlist.spotifyRadio = false;
    playlist.spotifyAlbumActive = false;
    playlist.micIndex = playlist.micQueue.length - 1;
    paintNpFromQueues();
    scrollToNowPlaying();
    return;
  }
  if (data.action === 'enqueue_queue' && data.ok && Array.isArray(data.queue)) {
    const rows = data.queue as QTrack[];
    if (!rows.length) return;
    const start = playlist.micQueue.length;
    playlist.micQueue = [...playlist.micQueue, ...rows];
    playlist.playListMode = 'mic';
    playlist.spotifyRadio = false;
    playlist.spotifyAlbumActive = false;
    playlist.micIndex = start;
    paintNpFromQueues();
    scrollToNowPlaying();
    return;
  }
  if (data.action === 'local_nav' && typeof data.delta === 'number') {
    const d = data.delta as number;
    const q = activeQueue();
    if (q.length <= 1) return;
    setActiveIndex(activeIndex() + d);
    paintNpFromQueues();
    return;
  }
  if (data.action === 'pause') {
    if (data.ok) playlist.spotifyPlaying = false;
    schedulePush();
  }
}

function applyRemoteData(d: Record<string, unknown>) {
  const incoming = JSON.stringify({
    micQueue: parseQueue(d.micQueue),
    radioQueue: parseQueue(d.radioQueue),
    albumQueue: parseQueue(d.albumQueue),
    micIndex: typeof d.micIndex === 'number' ? d.micIndex : 0,
    radioIndex: typeof d.radioIndex === 'number' ? d.radioIndex : 0,
    albumIndex: typeof d.albumIndex === 'number' ? d.albumIndex : 0,
    playListMode: parseMode(d.playListMode),
    spotifyRadio: !!d.spotifyRadio,
    spotifyAlbumActive: !!d.spotifyAlbumActive,
    spotifyPlaying: !!d.spotifyPlaying,
  });
  if (incoming === serializeSyncPayload()) return;

  applyingRemote = true;
  try {
    playlist.micQueue = parseQueue(d.micQueue);
    playlist.radioQueue = parseQueue(d.radioQueue);
    playlist.albumQueue = parseQueue(d.albumQueue);
    const mi = typeof d.micIndex === 'number' ? d.micIndex : 0;
    const ri = typeof d.radioIndex === 'number' ? d.radioIndex : 0;
    const ai = typeof d.albumIndex === 'number' ? d.albumIndex : 0;
    playlist.micIndex = Math.max(0, Math.min(Math.max(0, playlist.micQueue.length - 1), mi));
    playlist.radioIndex = Math.max(0, Math.min(Math.max(0, playlist.radioQueue.length - 1), ri));
    playlist.albumIndex = Math.max(0, Math.min(Math.max(0, playlist.albumQueue.length - 1), ai));
    playlist.playListMode = parseMode(d.playListMode);
    playlist.spotifyRadio = !!d.spotifyRadio;
    playlist.spotifyAlbumActive = !!d.spotifyAlbumActive;
    playlist.spotifyPlaying = !!d.spotifyPlaying;
    paintNpFromQueues();
  } finally {
    applyingRemote = false;
  }
}

export async function initPlaylistHub(): Promise<() => void> {
  docRef = null;
  if (unsub) {
    unsub();
    unsub = null;
  }
  if (pushTimer) {
    clearTimeout(pushTimer);
    pushTimer = null;
  }
  clearAdvanceTimer();

  let cfg: Record<string, unknown>;
  try {
    const r = await fetch('/api/config/firebase');
    cfg = (await r.json()) as Record<string, unknown>;
  } catch {
    return () => {};
  }
  if (!cfg.apiKey || typeof cfg.apiKey !== 'string') {
    return () => {};
  }

  try {
    const app = initializeApp(cfg as FirebaseOptions);
    const db = getFirestore(app);
    docRef = doc(db, 'ejdersted', 'playlists');
  } catch {
    return () => {};
  }

  unsub = onSnapshot(docRef, (snap) => {
    if (!snap.exists()) return;
    if (snap.metadata.hasPendingWrites) return;
    const raw = snap.data();
    applyRemoteData(raw as Record<string, unknown>);
  });

  return () => {
    if (unsub) {
      unsub();
      unsub = null;
    }
    if (pushTimer) {
      clearTimeout(pushTimer);
      pushTimer = null;
    }
    clearAdvanceTimer();
    docRef = null;
  };
}
