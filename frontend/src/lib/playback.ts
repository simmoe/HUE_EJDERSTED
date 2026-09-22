/**
 * Kiosk Spotify session: the speaker owns the timeline.
 * The kiosk starts playback on an explicit play, then follows now-playing.
 * It never starts the next track because a local clock expired.
 */

export type TrackRef = { uri: string };

export type SpeakerSnapshot = {
  uri: string;
  isPlaying: boolean;
  progressMs?: number;
  durationMs?: number;
};

export type ObserveInput = {
  queue: TrackRef[];
  activeIndex: number;
  assumedPlaying: boolean;
  /** URI we just asked the speaker to start; ignore stale snapshots until it lands. */
  startingUri: string;
  speaker: SpeakerSnapshot | null;
};

export type ObserveResult =
  | { type: 'ignore' }
  | { type: 'paused'; index?: number }
  | { type: 'ended' }
  | { type: 'idle' }
  | { type: 'follow'; index: number };

/** True when the speaker stopped because the current track ran out. */
export function trackHasEnded(speaker: SpeakerSnapshot): boolean {
  if (speaker.isPlaying) return false;
  const duration = speaker.durationMs ?? 0;
  const progress = speaker.progressMs ?? 0;
  return duration > 0 && progress >= Math.max(0, duration - 2000);
}

export function remainingUris(queue: TrackRef[], index: number): string[] {
  if (!Array.isArray(queue) || index < 0) return [];
  return queue
    .slice(index)
    .map((row) => row.uri)
    .filter((uri) => typeof uri === 'string' && uri.startsWith('spotify:track:'));
}

export function indexOfUri(queue: TrackRef[], uri: string): number {
  if (!uri) return -1;
  return queue.findIndex((row) => row.uri === uri);
}

export function startHasLanded(startingUri: string, speaker: SpeakerSnapshot | null): boolean {
  return !!startingUri && !!speaker?.isPlaying && speaker.uri === startingUri;
}

export function observeSpeaker(input: ObserveInput): ObserveResult {
  const { queue, activeIndex, assumedPlaying, startingUri, speaker } = input;

  if (startingUri) {
    if (startHasLanded(startingUri, speaker)) {
      const idx = indexOfUri(queue, startingUri);
      if (idx >= 0 && idx !== activeIndex) return { type: 'follow', index: idx };
      return { type: 'ignore' };
    }
    return { type: 'ignore' };
  }

  if (!speaker?.uri) {
    return assumedPlaying ? { type: 'idle' } : { type: 'ignore' };
  }

  const idx = indexOfUri(queue, speaker.uri);

  if (speaker.isPlaying) {
    if (idx < 0) return { type: 'ignore' };
    if (idx !== activeIndex) return { type: 'follow', index: idx };
    return { type: 'ignore' };
  }

  // Paused on the speaker (kiosk, iPhone, or anywhere else). Mid-track pause
  // stays paused. A track that ran out can start the next queued URI.
  if (idx < 0) {
    return assumedPlaying ? { type: 'idle' } : { type: 'ignore' };
  }
  if (!assumedPlaying && idx === activeIndex) return { type: 'ignore' };
  if (idx === activeIndex && assumedPlaying && trackHasEnded(speaker)) {
    return { type: 'ended' };
  }
  return idx === activeIndex ? { type: 'paused' } : { type: 'paused', index: idx };
}
