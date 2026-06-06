import { initializeApp, getApp, getApps, type FirebaseOptions } from 'firebase/app';
import {
  getFirestore,
  doc,
  onSnapshot,
  runTransaction,
  serverTimestamp,
  type DocumentReference,
  type Unsubscribe,
} from 'firebase/firestore';
import type { QTrack } from '$lib/playlistHub.svelte';

export type RadioPlaylist = {
  id: string;
  name: string;
  seedName: string;
  seedArtist: string;
  tracks: QTrack[];
  createdAt: number;
  updatedAt: number;
};

export const radioLibrary = $state({
  playlists: [] as RadioPlaylist[],
  loading: true,
  error: '',
});

let docRef: DocumentReference | null = null;
let unsub: Unsubscribe | null = null;

function parseTrack(x: unknown): QTrack | null {
  if (!x || typeof x !== 'object') return null;
  const row = x as Record<string, unknown>;
  const uri = typeof row.uri === 'string' ? row.uri : '';
  if (!uri.startsWith('spotify:track:')) return null;
  return {
    uri,
    name: typeof row.name === 'string' ? row.name : '',
    artist: typeof row.artist === 'string' ? row.artist : '',
  };
}

function parsePlaylist(x: unknown): RadioPlaylist | null {
  if (!x || typeof x !== 'object') return null;
  const row = x as Record<string, unknown>;
  const id = typeof row.id === 'string' ? row.id : '';
  const tracks = Array.isArray(row.tracks) ? row.tracks.map(parseTrack).filter((t): t is QTrack => !!t) : [];
  if (!id || tracks.length === 0) return null;
  return {
    id,
    name: typeof row.name === 'string' && row.name ? row.name : 'Radio',
    seedName: typeof row.seedName === 'string' ? row.seedName : '',
    seedArtist: typeof row.seedArtist === 'string' ? row.seedArtist : '',
    tracks,
    createdAt: typeof row.createdAt === 'number' ? row.createdAt : 0,
    updatedAt: typeof row.updatedAt === 'number' ? row.updatedAt : 0,
  };
}

function parseItems(raw: unknown): RadioPlaylist[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map(parsePlaylist)
    .filter((p): p is RadioPlaylist => !!p)
    .sort((a, b) => b.createdAt - a.createdAt);
}

function makeId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function playlistName(seedName: string, seedArtist: string): string {
  const title = seedName.trim() || 'Radio';
  const artist = seedArtist.trim();
  return artist ? `${title} - ${artist}` : title;
}

async function ensureDocRef(): Promise<DocumentReference> {
  if (docRef) return docRef;
  const r = await fetch('/api/config/firebase');
  const cfg = (await r.json()) as FirebaseOptions;
  if (!cfg.apiKey) throw new Error('Firebase mangler konfiguration');
  const app = getApps().length ? getApp() : initializeApp(cfg);
  docRef = doc(getFirestore(app), 'ejdersted', 'radioPlaylists');
  return docRef;
}

export async function initRadioLibrary(): Promise<() => void> {
  radioLibrary.loading = radioLibrary.playlists.length === 0;
  radioLibrary.error = '';
  try {
    const ref = await ensureDocRef();
    if (unsub) {
      unsub();
      unsub = null;
    }
    unsub = onSnapshot(ref, (snap) => {
      radioLibrary.playlists = parseItems(snap.exists() ? snap.data().items : []);
      radioLibrary.loading = false;
      radioLibrary.error = '';
    }, (err) => {
      radioLibrary.loading = false;
      radioLibrary.error = err.message || 'Kunne ikke hente radio-playlister';
    });
  } catch (e) {
    radioLibrary.loading = false;
    radioLibrary.error = (e as Error).message || 'Kunne ikke starte radio-bibliotek';
  }

  return () => {
    if (unsub) {
      unsub();
      unsub = null;
    }
  };
}

export async function saveRadioPlaylist(seed: QTrack, tracks: QTrack[]): Promise<RadioPlaylist> {
  const ref = await ensureDocRef();
  const seen = new Set<string>();
  const cleanTracks = tracks
    .map(parseTrack)
    .filter((t): t is QTrack => !!t)
    .filter((track) => {
      if (seen.has(track.uri)) return false;
      seen.add(track.uri);
      return true;
    });
  if (cleanTracks.length === 0) throw new Error('Ingen tracks at gemme');

  const now = Date.now();
  const saved: RadioPlaylist = {
    id: makeId(),
    name: playlistName(seed.name, seed.artist),
    seedName: seed.name,
    seedArtist: seed.artist,
    tracks: cleanTracks,
    createdAt: now,
    updatedAt: now,
  };

  await runTransaction(getFirestore(ref.firestore.app), async (tx) => {
    const snap = await tx.get(ref);
    const items = parseItems(snap.exists() ? snap.data().items : []);
    tx.set(ref, {
      items: [saved, ...items],
      updatedAt: serverTimestamp(),
    }, { merge: true });
  });
  return saved;
}

export async function deleteRadioPlaylist(id: string): Promise<void> {
  const ref = await ensureDocRef();
  await runTransaction(getFirestore(ref.firestore.app), async (tx) => {
    const snap = await tx.get(ref);
    const items = parseItems(snap.exists() ? snap.data().items : []);
    tx.set(ref, {
      items: items.filter((p) => p.id !== id),
      updatedAt: serverTimestamp(),
    }, { merge: true });
  });
}

export async function deleteRadioTrack(playlistId: string, index: number): Promise<void> {
  const ref = await ensureDocRef();
  await runTransaction(getFirestore(ref.firestore.app), async (tx) => {
    const snap = await tx.get(ref);
    const items = parseItems(snap.exists() ? snap.data().items : []);
    const now = Date.now();
    const next = items
      .map((p) => {
        if (p.id !== playlistId) return p;
        return {
          ...p,
          tracks: p.tracks.filter((_, i) => i !== index),
          updatedAt: now,
        };
      })
      .filter((p) => p.tracks.length > 0);
    tx.set(ref, {
      items: next,
      updatedAt: serverTimestamp(),
    }, { merge: true });
  });
}
