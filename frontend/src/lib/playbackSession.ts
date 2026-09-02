/**
 * One playback session for both hubs. Engines (Spotify Connect, DLNA, mpg123,
 * ffmpeg, librespot) are interchangeable backends; they must not own UI state.
 */
export type ActiveTransport = 'spotify' | 'podcast' | '';

export type SessionNowPlaying = {
  title: string;
  artist: string;
  fromSpeaker: boolean;
};

export function nowPlayingFromSession(input: {
  transport: ActiveTransport;
  podcastTitle: string;
  podcastArtist: string;
  musicTitle: string;
  musicArtist: string;
  speakerTitle?: string;
  speakerArtist?: string;
}): SessionNowPlaying {
  if (input.transport === 'podcast' && input.podcastTitle) {
    return { title: input.podcastTitle, artist: input.podcastArtist, fromSpeaker: false };
  }
  if (input.transport === 'spotify' && input.musicTitle) {
    return { title: input.musicTitle, artist: input.musicArtist, fromSpeaker: false };
  }
  if (input.speakerTitle) {
    return {
      title: input.speakerTitle,
      artist: input.speakerArtist || '',
      fromSpeaker: true,
    };
  }
  if (input.podcastTitle) {
    return { title: input.podcastTitle, artist: input.podcastArtist, fromSpeaker: false };
  }
  if (input.musicTitle) {
    return { title: input.musicTitle, artist: input.musicArtist, fromSpeaker: false };
  }
  return { title: '', artist: '', fromSpeaker: false };
}

/**
 * Poll snapshots from the podcast engine must not steal a claimed music session.
 * User-initiated podcast plays always adopt.
 */
export type PlaylistTrackRef = { uri: string; name?: string; artist?: string };

/**
 * The drill list comes from the shared library. Runtime player state can be an
 * older snapshot. Never play by index into that stale queue.
 */
export function resolvePlaylistTap(
  tapped: PlaylistTrackRef,
  visibleTracks: PlaylistTrackRef[],
  runtimeQueue: PlaylistTrackRef[],
  tappedIndex: number,
): { uri: string; queue: PlaylistTrackRef[]; index: number } | null {
  const uri = (tapped.uri || '').trim();
  if (!uri.startsWith('spotify:track:')) return null;
  const queue = visibleTracks.filter((t) => (t.uri || '').startsWith('spotify:track:'));
  if (queue.length === 0) return null;
  const byUri = queue.findIndex((t) => t.uri === uri);
  const index = byUri >= 0 ? byUri : Math.max(0, Math.min(queue.length - 1, tappedIndex));
  const chosen = byUri >= 0 ? queue[byUri] : queue[index];
  if (!chosen?.uri.startsWith('spotify:track:')) return null;
  void runtimeQueue;
  return { uri: chosen.uri, queue, index };
}

export function podcastPollAction(
  transport: ActiveTransport,
  playerActive: boolean,
  userInitiated = false,
): 'adopt' | 'ignore' | 'keep-paused' {
  if (userInitiated) return 'adopt';
  if (transport === 'spotify') return playerActive ? 'ignore' : 'keep-paused';
  return 'adopt';
}
