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
import { resolveCover } from '$lib/coverArt';

export type SavedAlbum = {
  id: string;
  name: string;
  seedName: string;
  seedArtist: string;
  coverUrl?: string;
  tracks: QTrack[];
  createdAt: number;
  updatedAt: number;
};

export const albumLibrary = $state({
  albums: [] as SavedAlbum[],
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

function parseAlbum(x: unknown): SavedAlbum | null {
  if (!x || typeof x !== 'object') return null;
  const row = x as Record<string, unknown>;
  const id = typeof row.id === 'string' ? row.id : '';
  const tracks = Array.isArray(row.tracks) ? row.tracks.map(parseTrack).filter((t): t is QTrack => !!t) : [];
  if (!id || tracks.length === 0) return null;
  const name = typeof row.name === 'string' && row.name ? row.name : 'Album';
  return {
    id,
    name,
    seedName: typeof row.seedName === 'string' && row.seedName ? row.seedName : name,
    seedArtist: typeof row.seedArtist === 'string' ? row.seedArtist : '',
    coverUrl: typeof row.coverUrl === 'string' ? row.coverUrl : '',
    tracks,
    createdAt: typeof row.createdAt === 'number' ? row.createdAt : 0,
    updatedAt: typeof row.updatedAt === 'number' ? row.updatedAt : 0,
  };
}

function parseItems(raw: unknown): SavedAlbum[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map(parseAlbum)
    .filter((p): p is SavedAlbum => !!p)
    .sort((a, b) => b.createdAt - a.createdAt);
}

function makeId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

async function ensureDocRef(): Promise<DocumentReference> {
  if (docRef) return docRef;
  const r = await fetch('/api/config/firebase');
  const cfg = (await r.json()) as FirebaseOptions;
  if (!cfg.apiKey) throw new Error('Firebase mangler konfiguration');
  const app = getApps().length ? getApp() : initializeApp(cfg);
  docRef = doc(getFirestore(app), 'ejdersted', 'savedAlbums');
  return docRef;
}

export async function initAlbumLibrary(): Promise<() => void> {
  albumLibrary.loading = albumLibrary.albums.length === 0;
  albumLibrary.error = '';
  try {
    const ref = await ensureDocRef();
    if (unsub) {
      unsub();
      unsub = null;
    }
    unsub = onSnapshot(ref, (snap) => {
      albumLibrary.albums = parseItems(snap.exists() ? snap.data().items : []);
      albumLibrary.loading = false;
      albumLibrary.error = '';
      void fillMissingCovers(albumLibrary.albums);
    }, (err) => {
      albumLibrary.loading = false;
      albumLibrary.error = err.message || 'Kunne ikke hente album';
    });
  } catch (e) {
    albumLibrary.loading = false;
    albumLibrary.error = (e as Error).message || 'Kunne ikke starte album-bibliotek';
  }

  return () => {
    if (unsub) {
      unsub();
      unsub = null;
    }
  };
}

let fillingCovers = false;

async function patchCover(id: string, coverUrl: string): Promise<void> {
  const ref = await ensureDocRef();
  await runTransaction(getFirestore(ref.firestore.app), async (tx) => {
    const snap = await tx.get(ref);
    const items = parseItems(snap.exists() ? snap.data().items : []);
    const next = items.map((p) => (p.id === id && !p.coverUrl ? { ...p, coverUrl } : p));
    tx.set(ref, { items: next, updatedAt: serverTimestamp() }, { merge: true });
  });
}

async function fillMissingCovers(items: SavedAlbum[]): Promise<void> {
  if (fillingCovers) return;
  const missing = items.filter((p) => !p.coverUrl);
  if (!missing.length) return;
  fillingCovers = true;
  try {
    for (const p of missing) {
      const coverUrl = await resolveCover(p.seedArtist, p.seedName || p.name);
      if (!coverUrl) continue;
      await patchCover(p.id, coverUrl);
    }
  } finally {
    fillingCovers = false;
  }
}

export async function saveAlbumCopy(input: {
  name: string;
  artist: string;
  coverUrl?: string;
  tracks: QTrack[];
}): Promise<SavedAlbum> {
  const ref = await ensureDocRef();
  const seen = new Set<string>();
  const cleanTracks = input.tracks
    .map(parseTrack)
    .filter((t): t is QTrack => !!t)
    .filter((track) => {
      if (seen.has(track.uri)) return false;
      seen.add(track.uri);
      return true;
    });
  if (cleanTracks.length === 0) throw new Error('Ingen sange at gemme');

  const now = Date.now();
  const name = input.name.trim() || 'Album';
  const artist = input.artist.trim();
  const coverUrl = input.coverUrl || await resolveCover(artist, name);
  const saved: SavedAlbum = {
    id: makeId(),
    name,
    seedName: name,
    seedArtist: artist,
    coverUrl,
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

export async function deleteSavedAlbum(id: string): Promise<void> {
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

export async function deleteSavedAlbumTrack(albumId: string, index: number): Promise<void> {
  const ref = await ensureDocRef();
  await runTransaction(getFirestore(ref.firestore.app), async (tx) => {
    const snap = await tx.get(ref);
    const items = parseItems(snap.exists() ? snap.data().items : []);
    const now = Date.now();
    const next = items
      .map((p) => {
        if (p.id !== albumId) return p;
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

export function sameTrackList(a: QTrack[], b: QTrack[]): boolean {
  if (a.length !== b.length || a.length === 0) return false;
  return a.every((track, i) => track.uri === b[i]?.uri);
}
