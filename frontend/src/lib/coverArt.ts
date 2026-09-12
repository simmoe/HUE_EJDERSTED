/**
 * Album covers via the iTunes Search API — no key, CORS, and it has been
 * stable for a decade. Spotify already gave us the track; this only paints it.
 */

const cache = new Map<string, string>();

function key(artist: string, title: string): string {
  return `${artist.trim().toLowerCase()}|${title.trim().toLowerCase()}`;
}

function enlarge(url: string): string {
  return url.replace(/\/\d+x\d+\w*\./, '/600x600bb.');
}

function score(row: { trackName?: string; artistName?: string }, artist: string, title: string): number {
  const t = (row.trackName || '').toLowerCase();
  const a = (row.artistName || '').toLowerCase();
  const wantT = title.trim().toLowerCase();
  const wantA = artist.trim().toLowerCase();
  let n = 0;
  if (wantT && t === wantT) n += 3;
  else if (wantT && t.includes(wantT)) n += 1;
  if (wantA && a === wantA) n += 3;
  else if (wantA && a.includes(wantA)) n += 1;
  return n;
}

export async function resolveCover(artist: string, title: string): Promise<string> {
  const cacheKey = key(artist, title);
  if (cache.has(cacheKey)) return cache.get(cacheKey) || '';
  const term = [title, artist].filter((part) => part.trim()).join(' ').trim();
  if (!term) {
    cache.set(cacheKey, '');
    return '';
  }
  try {
    const url =
      `https://itunes.apple.com/search?term=${encodeURIComponent(term)}` +
      `&media=music&entity=song&limit=5&country=dk`;
    const data = (await (await fetch(url)).json()) as {
      results?: { trackName?: string; artistName?: string; artworkUrl100?: string }[];
    };
    const rows = data.results || [];
    const best = [...rows].sort((x, y) => score(y, artist, title) - score(x, artist, title))[0];
    const art = enlarge(best?.artworkUrl100 || '');
    cache.set(cacheKey, art);
    return art;
  } catch {
    cache.set(cacheKey, '');
    return '';
  }
}
