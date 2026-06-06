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
import { showFeedback } from '$lib/feedback.svelte';

export type QTrack = { uri: string; name: string; artist: string };
export type VoiceHandleResult = { handled: boolean; message?: string; error?: string };

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
  savedPlaylistQueue: [] as QTrack[],
  micIndex: 0,
  radioIndex: 0,
  albumIndex: 0,
  savedPlaylistIndex: 0,
  playListMode: 'mic' as 'mic' | 'radio' | 'album' | 'playlist',
  spotifyRadio: false,
  spotifyRadioLoading: false,
  spotifyRadioError: '',
  spotifyAlbumActive: false,
  spotifyAlbumLoading: false,
  spotifyAlbumError: '',
  savedPlaylistActive: false,
  savedPlaylistTitle: '',
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

const ADVANCE_BUFFER_MS = 1300;
let advanceTimer: ReturnType<typeof setTimeout> | null = null;
let pausedRemainingMs = 0;

async function apiJson<T>(input: RequestInfo | URL, init: RequestInit = {}, timeoutMs = 15_000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(input, { ...init, signal: controller.signal });
    let data: unknown = {};
    try {
      data = await r.json();
    } catch {
      /* Ignore malformed gateway responses; caller gets HTTP status below. */
    }
    if (!r.ok) {
      const err = data as { error?: unknown; detail?: unknown };
      throw new Error(String(err.error || err.detail || `HTTP ${r.status}`));
    }
    return data as T;
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw new Error('Spotify svarede ikke i tide');
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

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

function parseMode(x: unknown): 'mic' | 'radio' | 'album' | 'playlist' {
  if (x === 'radio' || x === 'album' || x === 'mic' || x === 'playlist') return x;
  return 'mic';
}

function serializeSyncPayload(): string {
  const p = playlist;
  return JSON.stringify({
    micQueue: p.micQueue,
    radioQueue: p.radioQueue,
    albumQueue: p.albumQueue,
    savedPlaylistQueue: p.savedPlaylistQueue,
    micIndex: p.micIndex,
    radioIndex: p.radioIndex,
    albumIndex: p.albumIndex,
    savedPlaylistIndex: p.savedPlaylistIndex,
    playListMode: p.playListMode,
    spotifyRadio: p.spotifyRadio,
    spotifyAlbumActive: p.spotifyAlbumActive,
    savedPlaylistActive: p.savedPlaylistActive,
    savedPlaylistTitle: p.savedPlaylistTitle,
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
    savedPlaylistQueue: p.savedPlaylistQueue,
    micIndex: p.micIndex,
    radioIndex: p.radioIndex,
    albumIndex: p.albumIndex,
    savedPlaylistIndex: p.savedPlaylistIndex,
    playListMode: p.playListMode,
    spotifyRadio: p.spotifyRadio,
    spotifyAlbumActive: p.spotifyAlbumActive,
    savedPlaylistActive: p.savedPlaylistActive,
    savedPlaylistTitle: p.savedPlaylistTitle,
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
  if (playlist.playListMode === 'playlist') return playlist.savedPlaylistQueue;
  return playlist.micQueue;
}

export function activeIndex(): number {
  if (playlist.playListMode === 'radio') return playlist.radioIndex;
  if (playlist.playListMode === 'album') return playlist.albumIndex;
  if (playlist.playListMode === 'playlist') return playlist.savedPlaylistIndex;
  return playlist.micIndex;
}

function setActiveIndex(i: number) {
  const q = activeQueue();
  const n = Math.max(0, Math.min(q.length - 1, i));
  if (playlist.playListMode === 'radio') playlist.radioIndex = n;
  else if (playlist.playListMode === 'album') playlist.albumIndex = n;
  else if (playlist.playListMode === 'playlist') playlist.savedPlaylistIndex = n;
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

export async function playFromCurrentIndex(): Promise<boolean> {
  const q = activeQueue();
  const idx = activeIndex();
  const uri = q[idx]?.uri;
  if (!uri?.startsWith('spotify:track:')) return false;
  try {
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

/**
 * Kald denne FØR du starter ekstern afspilning (fx en podcast) som overtager B&O M5'eren.
 * Stopper auto-advance timeren så musik-køen ikke spammer over podcasten,
 * og opdaterer UI-state så afspilleren ikke står og lyver.
 */
export function stopMusicForExternalPlayback() {
  clearAdvanceTimer();
  pausedRemainingMs = 0;
  playlist.spotifyPlaying = false;
  schedulePush();
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
  const seed = seedTrackFromDisplayedNp();
  if (!seed?.uri) {
    playlist.spotifyRadioError = 'Ingen sang på afspilleren — vælg spor med forrige/næste eller tilføj til køen';
    showFeedback(playlist.spotifyRadioError, { kind: 'error' });
    return;
  }
  playlist.spotifyRadioLoading = true;
  playlist.spotifyRadioError = '';
  playlist.spotifyAlbumActive = false;
  playlist.savedPlaylistActive = false;
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
      showFeedback(playlist.spotifyRadioError, { kind: 'error' });
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
    showFeedback(playlist.spotifyRadioError, { kind: 'error' });
  } finally {
    playlist.spotifyRadioLoading = false;
  }
}

export async function playAlbum() {
  if (playlist.spotifyAlbumActive) {
    playlist.spotifyAlbumActive = false;
    playlist.spotifyAlbumError = '';
    playlist.playListMode = 'mic';
    playlist.savedPlaylistActive = false;
    paintNpFromQueues();
    return;
  }
  playlist.spotifyAlbumError = '';
  const uri = seedUriForAlbumBuild();
  if (!uri) {
    playlist.spotifyAlbumError = 'Vælg et track i køen';
    showFeedback(playlist.spotifyAlbumError, { kind: 'error' });
    return;
  }
  await pausePlaybackNow();
  playlist.spotifyAlbumLoading = true;
  playlist.spotifyRadio = false;
  playlist.savedPlaylistActive = false;
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
      showFeedback(playlist.spotifyAlbumError, { kind: 'error' });
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
    showFeedback(playlist.spotifyAlbumError, { kind: 'error' });
  } finally {
    playlist.spotifyAlbumLoading = false;
  }
}

export async function playSavedPlaylist(playlistUri: string, title = '') {
  if (!playlistUri) return { ok: false, error: 'Mangler playlist' };
  await pausePlaybackNow();
  playlist.spotifyRadio = false;
  playlist.spotifyAlbumActive = false;
  playlist.savedPlaylistActive = false;
  try {
    const data = await apiJson<{ ok?: boolean; error?: string; queue?: QTrack[] }>('/api/spotify/playlist/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playlist_uri: playlistUri }),
    }, 15_000);
    if (!data.ok) {
      return { ok: false, error: (data.error as string) ?? 'Playliste fejlede' };
    }
    playlist.savedPlaylistQueue = (data.queue as QTrack[]) ?? [];
    playlist.savedPlaylistIndex = 0;
    playlist.savedPlaylistActive = true;
    playlist.savedPlaylistTitle = title;
    playlist.playListMode = 'playlist';
    paintNpFromQueues();
    scrollToNowPlaying();
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message || 'Ingen forbindelse til hub' };
  }
}

export function handleVoicePayload(data: Record<string, unknown>): VoiceHandleResult {
  if (data.ok === false) {
    return {
      handled: false,
      error: typeof data.error === 'string' && data.error ? data.error : 'Stemmesøgning fejlede',
    };
  }

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
    playlist.savedPlaylistActive = false;
    playlist.micIndex = playlist.micQueue.length - 1;
    clearAdvanceTimer();
    pausedRemainingMs = 0;
    paintNpFromQueues();
    scrollToNowPlaying();
    return { handled: true, message: row.name || 'Tilføjet til kø' };
  }
  if (data.action === 'enqueue_queue' && data.ok && Array.isArray(data.queue)) {
    const rows = data.queue as QTrack[];
    if (!rows.length) return { handled: false, error: 'Fandt ingen sange i køen' };
    const start = playlist.micQueue.length;
    playlist.micQueue = [...playlist.micQueue, ...rows];
    playlist.playListMode = 'mic';
    playlist.spotifyRadio = false;
    playlist.spotifyAlbumActive = false;
    playlist.savedPlaylistActive = false;
    playlist.micIndex = start;
    clearAdvanceTimer();
    pausedRemainingMs = 0;
    paintNpFromQueues();
    scrollToNowPlaying();
    return {
      handled: true,
      message: typeof data.label === 'string' && data.label ? data.label : rows[0]?.name || 'Tilføjet til kø',
    };
  }
  if (data.action === 'local_nav' && typeof data.delta === 'number') {
    const d = data.delta as number;
    const q = activeQueue();
    if (q.length <= 1) return { handled: false, error: 'Ingen næste sang i køen' };
    setActiveIndex(activeIndex() + d);
    paintNpFromQueues();
    return { handled: true };
  }
  if (data.action === 'pause') {
    if (data.ok) playlist.spotifyPlaying = false;
    schedulePush();
    return data.ok
      ? { handled: true, message: 'pause' }
      : { handled: false, error: 'Pause fejlede' };
  }
  if (data.action === 'use_play_button') {
    return { handled: true, message: 'Tryk play' };
  }

  return {
    handled: false,
    error: typeof data.error === 'string' && data.error ? data.error : 'Kommandoen blev ikke forstået',
  };
}

function applyRemoteData(d: Record<string, unknown>) {
  const incoming = JSON.stringify({
    micQueue: parseQueue(d.micQueue),
    radioQueue: parseQueue(d.radioQueue),
    albumQueue: parseQueue(d.albumQueue),
    savedPlaylistQueue: parseQueue(d.savedPlaylistQueue),
    micIndex: typeof d.micIndex === 'number' ? d.micIndex : 0,
    radioIndex: typeof d.radioIndex === 'number' ? d.radioIndex : 0,
    albumIndex: typeof d.albumIndex === 'number' ? d.albumIndex : 0,
    savedPlaylistIndex: typeof d.savedPlaylistIndex === 'number' ? d.savedPlaylistIndex : 0,
    playListMode: parseMode(d.playListMode),
    spotifyRadio: !!d.spotifyRadio,
    spotifyAlbumActive: !!d.spotifyAlbumActive,
    savedPlaylistActive: !!d.savedPlaylistActive,
    savedPlaylistTitle: typeof d.savedPlaylistTitle === 'string' ? d.savedPlaylistTitle : '',
    spotifyPlaying: !!d.spotifyPlaying,
  });
  if (incoming === serializeSyncPayload()) return;

  applyingRemote = true;
  try {
    playlist.micQueue = parseQueue(d.micQueue);
    playlist.radioQueue = parseQueue(d.radioQueue);
    playlist.albumQueue = parseQueue(d.albumQueue);
    playlist.savedPlaylistQueue = parseQueue(d.savedPlaylistQueue);
    const mi = typeof d.micIndex === 'number' ? d.micIndex : 0;
    const ri = typeof d.radioIndex === 'number' ? d.radioIndex : 0;
    const ai = typeof d.albumIndex === 'number' ? d.albumIndex : 0;
    const pi = typeof d.savedPlaylistIndex === 'number' ? d.savedPlaylistIndex : 0;
    playlist.micIndex = Math.max(0, Math.min(Math.max(0, playlist.micQueue.length - 1), mi));
    playlist.radioIndex = Math.max(0, Math.min(Math.max(0, playlist.radioQueue.length - 1), ri));
    playlist.albumIndex = Math.max(0, Math.min(Math.max(0, playlist.albumQueue.length - 1), ai));
    playlist.savedPlaylistIndex = Math.max(0, Math.min(Math.max(0, playlist.savedPlaylistQueue.length - 1), pi));
    playlist.playListMode = parseMode(d.playListMode);
    playlist.spotifyRadio = !!d.spotifyRadio;
    playlist.spotifyAlbumActive = !!d.spotifyAlbumActive;
    playlist.savedPlaylistActive = !!d.savedPlaylistActive;
    playlist.savedPlaylistTitle = typeof d.savedPlaylistTitle === 'string' ? d.savedPlaylistTitle : '';
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
