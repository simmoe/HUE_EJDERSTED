export type EvidenceItem = {
  id: string;
  createdAt: number | null;
  createdAtIso?: string | null;
  url: string;
};

export function evidenceImageUrl(id: string): string {
  return `/api/security/evidence/${id}.jpg`;
}

export function parseEvidenceList(payload: unknown): EvidenceItem[] {
  const items = payload && typeof payload === 'object' ? (payload as { items?: unknown }).items : null;
  if (!Array.isArray(items)) return [];
  const out: EvidenceItem[] = [];
  for (const row of items) {
    if (!row || typeof row !== 'object') continue;
    const id = typeof (row as { id?: unknown }).id === 'string' ? (row as { id: string }).id : '';
    if (!id || id.includes('/') || id.includes('..')) continue;
    const createdAt = typeof (row as { createdAt?: unknown }).createdAt === 'number'
      ? (row as { createdAt: number }).createdAt
      : null;
    const createdAtIso = typeof (row as { createdAtIso?: unknown }).createdAtIso === 'string'
      ? (row as { createdAtIso: string }).createdAtIso
      : null;
    const url = typeof (row as { url?: unknown }).url === 'string'
      ? (row as { url: string }).url
      : evidenceImageUrl(id);
    out.push({ id, createdAt, createdAtIso, url });
  }
  return out;
}

export function evidenceIdFromUrl(url: string): string {
  const local = url.match(/\/api\/security\/evidence\/([^/?#]+?)(?:\.jpg)?(?:[?#]|$)/i);
  if (local?.[1]) return decodeURIComponent(local[1]);
  const storage = url.match(/garden%2Fevents%2F([^%/?#]+)/i) || url.match(/garden\/events\/([^/?#]+)/i);
  if (storage?.[1]) return decodeURIComponent(storage[1]);
  return '';
}

export function indexOfEvidence(items: EvidenceItem[], url: string): number {
  const id = evidenceIdFromUrl(url);
  if (!id) return 0;
  const index = items.findIndex((item) => item.id === id);
  return index >= 0 ? index : 0;
}
