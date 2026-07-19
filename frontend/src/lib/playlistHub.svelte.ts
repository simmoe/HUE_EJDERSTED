/**
 * Delte playlister på tværs af browsere via Firestore.
 * Konfiguration hentes fra hub: GET /api/config/firebase (hub_globals.json).
 *
 * Firestore: fælles bibliotek i `ejdersted/radioPlaylists`, mens player-runtime
 * er site-specifik i `ejdersted/player_home` eller `ejdersted/player_garden`.
 *
 * Tilstand ligger i ét `playlist`-objekt (Svelte 5: eksporteret $state må ikke reassignedes som enkeltfelter).
 */
import { initializeApp, getApp, getApps, type FirebaseOptions } from 'firebase/app';
import {
  getFirestore,
  doc,
  getDoc,
  onSnapshot,
  setDoc,
  serverTimestamp,
  type Unsubscribe,
  type DocumentReference,
} from 'firebase/firestore';
import { init as initSpotifyWebPlayer } from '$lib/spotifyPlayer.svelte';
import { showFeedback } from '$lib/feedback.svelte';

export type QTrack = { uri: string; name: string; artist: string };
export type PodcastEpisode = {
  id: string;
  uri: string;
  name: string;
  release_date?: string;
  duration_ms: number;
};
export type VoiceHandleResult = { handled: boolean; message?: string; error?: string };
type PlaylistMode = 'mic' | 'radio' | 'album' | 'playlist';
type ActiveTransport = 'spotify' | 'podcast' | '';
type SyncPayload = {
  micQueue: QTrack[];
  radioQueue: QTrack[];
  albumQueue: QTrack[];
  savedPlaylistQueue: QTrack[];
  micIndex: number;
  radioIndex: number;
  albumIndex: number;
  savedPlaylistIndex: number;
  playListMode: PlaylistMode;
  spotifyRadio: boolean;
  spotifyAlbumActive: boolean;
  savedPlaylistActive: boolean;
  savedPlaylistTitle: string;
  spotifyPlaying: boolean;
  spotifyEndsAt: number;
  activeTransport: ActiveTransport;
  podcastQueue: PodcastEpisode[];
  podcastIndex: number;
  podcastShowTitle: string;
  podcastEpisodeTitle: string;
  podcastPlaying: boolean;
  podcastPositionMs: number;
  podcastDurationMs: number;
  podcastUpdatedAt: number;
};

export const playlist = $state({
  spotifyTitle: '',
  spotifyArtist: '',
  spotifyNextTitle: '',
  spotifyNextArtist: '',
  spotifyTrackUri: '',
  spotifyPlaying: false,
  spotifyEndsAt: 0,
  micQueue: [] as QTrack[],
  radioQueue: [] as QTrack[],
  albumQueue: [] as QTrack[],
  savedPlaylistQueue: [] as QTrack[],
  micIndex: 0,
  radioIndex: 0,
  albumIndex: 0,
  savedPlaylistIndex: 0,
  playListMode: 'mic' as PlaylistMode,
  spotifyRadio: false,
  spotifyRadioLoading: false,
  spotifyRadioError: '',
  spotifyAlbumActive: false,
  spotifyAlbumLoading: false,
  spotifyAlbumError: '',
  savedPlaylistActive: false,
  savedPlaylistTitle: '',
  activeTransport: '' as ActiveTransport,
  podcastQueue: [] as PodcastEpisode[],
  podcastIndex: 0,
  podcastShowTitle: '',
  podcastEpisodeTitle: '',
  podcastPlaying: false,
  podcastPositionMs: 0,
  podcastDurationMs: 0,
  podcastUpdatedAt: 0,
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
let playbackReconcileTimer: ReturnType<typeof setInterval> | null = null;

const ADVANCE_BUFFER_MS = 1300;
const FALLBACK_TRACK_DURATION_MS = 6 * 60_000;
let advanceTimer: ReturnType<typeof setTimeout> | null = null;
let pausedRemainingMs = 0;

function playerDocId(site: unknown): string {
  return site === 'garden' ? 'player_garden' : 'player_home';
}

async function currentSite(): Promise<string> {
  try {
    const r = await fetch('/api/config', { cache: 'no-store' });
    const cfg = (await r.json()) as { site?: unknown };
    return cfg.site === 'garden' ? 'garden' : 'home';
  } catch {
    return 'home';
  }
}

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

async function pauseSpotifyRemote() {
  try {
    await fetch('/api/spotify/pause', { method: 'POST' });
  } catch {
    /* The local state is still authoritative when the remote player is already idle. */
  }
}

function finishActiveQueuePlayback() {
  clearAdvanceTimer();
  pausedRemainingMs = 0;
  playlist.spotifyPlaying = false;
  playlist.spotifyEndsAt = 0;
  playlist.activeTransport = '';
  playlist.spotifyRadio = false;
  playlist.spotifyAlbumActive = false;
  playlist.savedPlaylistActive = false;
  playlist.spotifyNextTitle = '';
  playlist.spotifyNextArtist = '';
  schedulePush();
}

async function finishActiveQueuePlaybackAndPause() {
  await pauseSpotifyRemote();
  finishActiveQueuePlayback();
}

function reconcilePlaybackClock() {
  if (!playlist.spotifyPlaying || playlist.spotifyEndsAt <= 0) return;
  if (Date.now() < playlist.spotifyEndsAt) return;
  void finishActiveQueuePlaybackAndPause();
}

function scheduleAdvanceUntil(endsAt: number) {
  clearAdvanceTimer();
  pausedRemainingMs = 0;
  playlist.spotifyEndsAt = endsAt;
  const delay = Math.max(0, endsAt - Date.now());
  const startedAt = Date.now();
  advanceTimer = setTimeout(() => {
    advanceTimer = null;
    if (playlist.spotifyEndsAt > Date.now()) {
      scheduleAdvanceUntil(playlist.spotifyEndsAt);
      return;
    }
    const q = activeQueue();
    const idx = activeIndex();
    if (idx + 1 < q.length) {
      setActiveIndex(idx + 1);
      paintNpFromQueues();
      scrollToNowPlaying();
      playlist.spotifyEndsAt = 0;
      void playFromCurrentIndex().then((ok) => {
        if (!ok) void finishActiveQueuePlaybackAndPause();
      });
    } else {
      void finishActiveQueuePlaybackAndPause();
    }
  }, delay);
  (scheduleAdvance as any)._startedAt = startedAt;
  (scheduleAdvance as any)._totalMs = delay;
}

function scheduleAdvance(ms: number) {
  scheduleAdvanceUntil(Date.now() + ms + ADVANCE_BUFFER_MS);
}

function pauseAdvanceTimer() {
  if (!advanceTimer) return;
  const startedAt = (scheduleAdvance as any)._startedAt ?? 0;
  const totalMs = (scheduleAdvance as any)._totalMs ?? 0;
  const elapsed = Date.now() - startedAt;
  pausedRemainingMs = Math.max(0, totalMs - elapsed);
  clearAdvanceTimer();
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

function parseMode(x: unknown): PlaylistMode {
  if (x === 'radio' || x === 'album' || x === 'mic' || x === 'playlist') return x;
  return 'mic';
}

function parseTransport(x: unknown): ActiveTransport {
  if (x === 'spotify' || x === 'podcast') return x;
  return '';
}

function parsePodcastQueue(a: unknown): PodcastEpisode[] {
  if (!Array.isArray(a)) return [];
  return a
    .filter((ep) => typeof ep === 'object' && ep !== null && typeof (ep as { uri?: unknown }).uri === 'string')
    .map((ep) => {
      const row = ep as Record<string, unknown>;
      return {
        id: String(row.id ?? row.uri ?? ''),
        uri: String(row.uri ?? ''),
        name: String(row.name ?? ''),
        release_date: typeof row.release_date === 'string' ? row.release_date : '',
        duration_ms: typeof row.duration_ms === 'number' ? row.duration_ms : 0,
      };
    });
}

function currentSyncPayload(): SyncPayload {
  return {
    micQueue: playlist.micQueue,
    radioQueue: playlist.radioQueue,
    albumQueue: playlist.albumQueue,
    savedPlaylistQueue: playlist.savedPlaylistQueue,
    micIndex: playlist.micIndex,
    radioIndex: playlist.radioIndex,
    albumIndex: playlist.albumIndex,
    savedPlaylistIndex: playlist.savedPlaylistIndex,
    playListMode: playlist.playListMode,
    spotifyRadio: playlist.spotifyRadio,
    spotifyAlbumActive: playlist.spotifyAlbumActive,
    savedPlaylistActive: playlist.savedPlaylistActive,
    savedPlaylistTitle: playlist.savedPlaylistTitle,
    spotifyPlaying: playlist.spotifyPlaying,
    spotifyEndsAt: playlist.spotifyEndsAt,
    activeTransport: playlist.activeTransport,
    podcastQueue: playlist.podcastQueue,
    podcastIndex: playlist.podcastIndex,
    podcastShowTitle: playlist.podcastShowTitle,
    podcastEpisodeTitle: playlist.podcastEpisodeTitle,
    podcastPlaying: playlist.podcastPlaying,
    podcastPositionMs: playlist.podcastPositionMs,
    podcastDurationMs: playlist.podcastDurationMs,
    podcastUpdatedAt: playlist.podcastUpdatedAt,
  };
}

function serializeSyncPayload(payload = currentSyncPayload()): string {
  return JSON.stringify(payload);
}

function normalizeSyncData(d: Record<string, unknown>): SyncPayload {
  const player = (d.player && typeof d.player === 'object' ? d.player : {}) as Record<string, unknown>;
  const queues = (d.queues && typeof d.queues === 'object' ? d.queues : {}) as Record<string, unknown>;
  const indices = (d.indices && typeof d.indices === 'object' ? d.indices : {}) as Record<string, unknown>;
  const contexts = (d.contexts && typeof d.contexts === 'object' ? d.contexts : {}) as Record<string, unknown>;
  const transport = (d.transport && typeof d.transport === 'object' ? d.transport : {}) as Record<string, unknown>;
  const podcasts = (d.podcasts && typeof d.podcasts === 'object' ? d.podcasts : {}) as Record<string, unknown>;
  return {
    micQueue: parseQueue(queues.mic ?? d.micQueue),
    radioQueue: parseQueue(queues.radio ?? d.radioQueue),
    albumQueue: parseQueue(queues.album ?? d.albumQueue),
    savedPlaylistQueue: parseQueue(queues.savedPlaylist ?? d.savedPlaylistQueue),
    micIndex: typeof indices.mic === 'number' ? indices.mic : typeof d.micIndex === 'number' ? d.micIndex : 0,
    radioIndex: typeof indices.radio === 'number' ? indices.radio : typeof d.radioIndex === 'number' ? d.radioIndex : 0,
    albumIndex: typeof indices.album === 'number' ? indices.album : typeof d.albumIndex === 'number' ? d.albumIndex : 0,
    savedPlaylistIndex: typeof indices.savedPlaylist === 'number'
      ? indices.savedPlaylist
      : typeof d.savedPlaylistIndex === 'number'
        ? d.savedPlaylistIndex
        : 0,
    playListMode: parseMode(player.mode ?? d.playListMode),
    spotifyRadio: !!(contexts.radioActive ?? d.spotifyRadio),
    spotifyAlbumActive: !!(contexts.albumActive ?? d.spotifyAlbumActive),
    savedPlaylistActive: !!(contexts.savedPlaylistActive ?? d.savedPlaylistActive),
    savedPlaylistTitle: typeof contexts.savedPlaylistTitle === 'string'
      ? contexts.savedPlaylistTitle
      : typeof d.savedPlaylistTitle === 'string'
        ? d.savedPlaylistTitle
        : '',
    spotifyPlaying: !!(player.playing ?? d.spotifyPlaying),
    spotifyEndsAt: typeof player.endsAt === 'number'
      ? player.endsAt
      : typeof d.spotifyEndsAt === 'number'
        ? d.spotifyEndsAt
        : 0,
    activeTransport: parseTransport(transport.active ?? d.activeTransport),
    podcastQueue: parsePodcastQueue(podcasts.queue ?? d.podcastQueue),
    podcastIndex: typeof podcasts.index === 'number'
      ? podcasts.index
      : typeof d.podcastIndex === 'number'
        ? d.podcastIndex
        : 0,
    podcastShowTitle: typeof podcasts.showTitle === 'string'
      ? podcasts.showTitle
      : typeof d.podcastShowTitle === 'string'
        ? d.podcastShowTitle
        : '',
    podcastEpisodeTitle: typeof podcasts.episodeTitle === 'string'
      ? podcasts.episodeTitle
      : typeof d.podcastEpisodeTitle === 'string'
        ? d.podcastEpisodeTitle
        : '',
    podcastPlaying: !!(podcasts.playing ?? d.podcastPlaying),
    podcastPositionMs: typeof podcasts.positionMs === 'number'
      ? podcasts.positionMs
      : typeof d.podcastPositionMs === 'number'
        ? d.podcastPositionMs
        : 0,
    podcastDurationMs: typeof podcasts.durationMs === 'number'
      ? podcasts.durationMs
      : typeof d.podcastDurationMs === 'number'
        ? d.podcastDurationMs
        : 0,
    podcastUpdatedAt: typeof podcasts.updatedAt === 'number'
      ? podcasts.updatedAt
      : typeof d.podcastUpdatedAt === 'number'
        ? d.podcastUpdatedAt
        : 0,
  };
}

function schedulePush() {
  if (applyingRemote || !docRef) return;
  if (pushTimer) clearTimeout(pushTimer);
  pushTimer = setTimeout(() => {
    pushTimer = null;
    void flushPush();
  }, 400);
}

function pushImmediately() {
  if (pushTimer) clearTimeout(pushTimer);
  pushTimer = null;
  void flushPush();
}

async function flushPush() {
  if (!docRef || applyingRemote) return;
  const p = currentSyncPayload();
  const payload = {
    player: {
      mode: p.playListMode,
      playing: p.spotifyPlaying,
      endsAt: p.spotifyEndsAt,
    },
    queues: {
      mic: p.micQueue,
      radio: p.radioQueue,
      album: p.albumQueue,
      savedPlaylist: p.savedPlaylistQueue,
    },
    indices: {
      mic: p.micIndex,
      radio: p.radioIndex,
      album: p.albumIndex,
      savedPlaylist: p.savedPlaylistIndex,
    },
    contexts: {
      radioActive: p.spotifyRadio,
      albumActive: p.spotifyAlbumActive,
      savedPlaylistActive: p.savedPlaylistActive,
      savedPlaylistTitle: p.savedPlaylistTitle,
    },
    transport: {
      active: p.activeTransport,
    },
    podcasts: {
      queue: p.podcastQueue,
      index: p.podcastIndex,
      showTitle: p.podcastShowTitle,
      episodeTitle: p.podcastEpisodeTitle,
      playing: p.podcastPlaying,
      positionMs: p.podcastPositionMs,
      durationMs: p.podcastDurationMs,
      updatedAt: p.podcastUpdatedAt,
    },
    updatedAt: serverTimestamp(),
  };
  try {
    await setDoc(docRef, payload);
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

export function isPlaylistContextActive(): boolean {
  return playlist.playListMode === 'radio' || playlist.playListMode === 'playlist';
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

async function playTrackUri(uri: string): Promise<boolean> {
  if (!uri?.startsWith('spotify:track:')) return false;
  clearAdvanceTimer();
  pausedRemainingMs = 0;
  try {
    const r = await fetch('/api/spotify/play-uris', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uris: [uri], offset: 0, position_ms: 0 }),
    });
    const data = (await r.json()) as {
      ok: boolean;
      duration_ms?: number;
      detail?: string;
      error?: string;
    };
    if (data.ok) {
      clearPodcastTransport(false);
      playlist.activeTransport = 'spotify';
      playlist.spotifyPlaying = true;
      scheduleAdvance(data.duration_ms && data.duration_ms > 0 ? data.duration_ms : FALLBACK_TRACK_DURATION_MS);
      pushImmediately();
      return true;
    }
    showFeedback(data.detail || data.error || 'Afspilning fejlede', { kind: 'error' });
  } catch {
    showFeedback('Ingen forbindelse til hub', { kind: 'error' });
  }
  return false;
}

export async function playFromCurrentIndex(): Promise<boolean> {
  const q = activeQueue();
  const idx = activeIndex();
  const uri = q[idx]?.uri;
  const ok = await playTrackUri(uri);
  if (ok) scrollToNowPlaying();
  return ok;
}

export async function togglePlayPause() {
  if (playlist.spotifyPlaying) {
    try {
      const r = await fetch('/api/spotify/pause', { method: 'POST' });
      const data = await r.json();
      if (data.ok) {
        playlist.spotifyPlaying = false;
        playlist.spotifyEndsAt = 0;
        pauseAdvanceTimer();
      }
    } catch {
      /* */
    }
    schedulePush();
    return;
  }
  await playTrackUri(playlist.spotifyTrackUri);
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
  playlist.spotifyEndsAt = 0;
  playlist.activeTransport = 'podcast';
  schedulePush();
}

export function setPodcastTransportFromPlayer(player: Record<string, unknown>, push = true) {
  const queue = parsePodcastQueue(player.queue);
  const idx = typeof player.episodeIndex === 'number' ? player.episodeIndex : 0;
  playlist.activeTransport = player.active ? 'podcast' : '';
  playlist.podcastQueue = queue;
  playlist.podcastIndex = Math.max(0, Math.min(Math.max(0, queue.length - 1), idx));
  playlist.podcastShowTitle = String(player.showTitle ?? '');
  playlist.podcastEpisodeTitle = String(player.episodeTitle ?? '');
  playlist.podcastPlaying = !!player.playing;
  playlist.podcastPositionMs = typeof player.positionMs === 'number' ? player.positionMs : 0;
  playlist.podcastDurationMs = typeof player.durationMs === 'number' ? player.durationMs : 0;
  playlist.podcastUpdatedAt = Date.now();
  if (!player.active) {
    playlist.podcastQueue = [];
    playlist.podcastIndex = 0;
    playlist.podcastShowTitle = '';
    playlist.podcastEpisodeTitle = '';
    playlist.podcastPlaying = false;
    playlist.podcastPositionMs = 0;
    playlist.podcastDurationMs = 0;
  }
  if (push) schedulePush();
}

export function clearPodcastTransport(push = true) {
  playlist.activeTransport = '';
  playlist.podcastQueue = [];
  playlist.podcastIndex = 0;
  playlist.podcastShowTitle = '';
  playlist.podcastEpisodeTitle = '';
  playlist.podcastPlaying = false;
  playlist.podcastPositionMs = 0;
  playlist.podcastDurationMs = 0;
  playlist.podcastUpdatedAt = Date.now();
  if (push) schedulePush();
}

async function pausePlaybackNow() {
  try {
    await fetch('/api/spotify/pause', { method: 'POST' });
  } catch {
    /* */
  }
  playlist.spotifyPlaying = false;
  playlist.spotifyEndsAt = 0;
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

function detachCurrentTrackFromPlaylistContext() {
  const track = seedTrackFromDisplayedNp();
  if (!track) return false;
  playlist.micQueue = [track];
  playlist.micIndex = 0;
  playlist.playListMode = 'mic';
  playlist.spotifyRadio = false;
  playlist.spotifyAlbumActive = false;
  playlist.savedPlaylistActive = false;
  playlist.savedPlaylistTitle = '';
  playlist.spotifyNextTitle = '';
  playlist.spotifyNextArtist = '';
  paintNpFromQueues();
  schedulePush();
  return true;
}

export async function togglePlaylistContext() {
  if (isPlaylistContextActive()) {
    detachCurrentTrackFromPlaylistContext();
    return;
  }
  const seed = seedTrackFromDisplayedNp();
  if (!seed?.uri) {
    playlist.spotifyRadioError = 'Ingen sang på afspilleren — vælg spor med forrige/næste eller tilføj til køen';
    showFeedback(playlist.spotifyRadioError, { kind: 'error' });
    return;
  }
  playlist.spotifyRadioLoading = true;
  playlist.spotifyRadioError = '';
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
    if (!playlist.radioQueue.length) {
      playlist.spotifyRadio = false;
      playlist.spotifyRadioError = 'Radio gav ingen sange';
      showFeedback(playlist.spotifyRadioError, { kind: 'error' });
      return;
    }
    playlist.radioIndex = 0;
    playlist.spotifyRadio = true;
    playlist.spotifyAlbumActive = false;
    playlist.savedPlaylistActive = false;
    playlist.playListMode = 'radio';
    playlist.spotifyRadioError = '';
    paintNpFromQueues();
    scrollToNowPlaying();
    await playFromCurrentIndex();
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
    pushImmediately();
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
    pushImmediately();
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
  const incoming = normalizeSyncData(d);
  const isStructured = !!(d.player && d.queues && d.indices && d.contexts);
  if (serializeSyncPayload(incoming) === serializeSyncPayload()) {
    if (!isStructured) schedulePush();
    return;
  }

  let remotePlaybackAlreadyEnded = false;
  applyingRemote = true;
  try {
    playlist.micQueue = incoming.micQueue;
    playlist.radioQueue = incoming.radioQueue;
    playlist.albumQueue = incoming.albumQueue;
    playlist.savedPlaylistQueue = incoming.savedPlaylistQueue;
    const mi = incoming.micIndex;
    const ri = incoming.radioIndex;
    const ai = incoming.albumIndex;
    const pi = incoming.savedPlaylistIndex;
    playlist.micIndex = Math.max(0, Math.min(Math.max(0, playlist.micQueue.length - 1), mi));
    playlist.radioIndex = Math.max(0, Math.min(Math.max(0, playlist.radioQueue.length - 1), ri));
    playlist.albumIndex = Math.max(0, Math.min(Math.max(0, playlist.albumQueue.length - 1), ai));
    playlist.savedPlaylistIndex = Math.max(0, Math.min(Math.max(0, playlist.savedPlaylistQueue.length - 1), pi));
    playlist.playListMode = incoming.playListMode;
    playlist.spotifyRadio = incoming.spotifyRadio;
    playlist.spotifyAlbumActive = incoming.spotifyAlbumActive;
    playlist.savedPlaylistActive = incoming.savedPlaylistActive;
    playlist.savedPlaylistTitle = incoming.savedPlaylistTitle;
    playlist.spotifyPlaying = incoming.spotifyPlaying;
    playlist.spotifyEndsAt = incoming.spotifyEndsAt;
    playlist.activeTransport = incoming.activeTransport;
    playlist.podcastQueue = incoming.podcastQueue;
    playlist.podcastIndex = Math.max(0, Math.min(Math.max(0, playlist.podcastQueue.length - 1), incoming.podcastIndex));
    playlist.podcastShowTitle = incoming.podcastShowTitle;
    playlist.podcastEpisodeTitle = incoming.podcastEpisodeTitle;
    playlist.podcastPlaying = incoming.podcastPlaying;
    playlist.podcastPositionMs = incoming.podcastPositionMs;
    playlist.podcastDurationMs = incoming.podcastDurationMs;
    playlist.podcastUpdatedAt = incoming.podcastUpdatedAt;
    paintNpFromQueues();
    if (playlist.spotifyPlaying && playlist.spotifyEndsAt > Date.now()) {
      scheduleAdvanceUntil(playlist.spotifyEndsAt);
    } else if (playlist.spotifyPlaying && playlist.spotifyEndsAt > 0) {
      remotePlaybackAlreadyEnded = true;
      clearAdvanceTimer();
      pausedRemainingMs = 0;
      playlist.spotifyPlaying = false;
      playlist.spotifyEndsAt = 0;
      if (playlist.playListMode === 'radio') playlist.spotifyRadio = false;
      if (playlist.playListMode === 'playlist') playlist.savedPlaylistActive = false;
    } else if (!playlist.spotifyPlaying) {
      clearAdvanceTimer();
      pausedRemainingMs = 0;
    }
  } finally {
    applyingRemote = false;
  }
  if (remotePlaybackAlreadyEnded || !isStructured) schedulePush();
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
  if (playbackReconcileTimer) {
    clearInterval(playbackReconcileTimer);
    playbackReconcileTimer = null;
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
    const app = getApps().length ? getApp() : initializeApp(cfg as FirebaseOptions);
    const db = getFirestore(app);
    docRef = doc(db, 'ejdersted', playerDocId(await currentSite()));

    const playerSnap = await getDoc(docRef);
    if (!playerSnap.exists()) {
      const legacyRef = doc(db, 'ejdersted', 'playlists');
      const legacySnap = await getDoc(legacyRef);
      if (legacySnap.exists()) {
        await setDoc(docRef, {
          ...legacySnap.data(),
          migratedFrom: 'ejdersted/playlists',
          migratedAt: serverTimestamp(),
        });
      }
    }
  } catch {
    return () => {};
  }

  unsub = onSnapshot(docRef, (snap) => {
    if (!snap.exists()) return;
    if (snap.metadata.hasPendingWrites) return;
    const raw = snap.data();
    applyRemoteData(raw as Record<string, unknown>);
  });
  playbackReconcileTimer = setInterval(reconcilePlaybackClock, 1_000);
  reconcilePlaybackClock();
  return () => {
    if (unsub) {
      unsub();
      unsub = null;
    }
    if (pushTimer) {
      clearTimeout(pushTimer);
      pushTimer = null;
    }
    if (playbackReconcileTimer) {
      clearInterval(playbackReconcileTimer);
      playbackReconcileTimer = null;
    }
    clearAdvanceTimer();
    docRef = null;
  };
}
