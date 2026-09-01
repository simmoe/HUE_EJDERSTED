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
export function podcastPollAction(
  transport: ActiveTransport,
  playerActive: boolean,
  userInitiated = false,
): 'adopt' | 'ignore' | 'keep-paused' {
  if (userInitiated) return 'adopt';
  if (transport === 'spotify') return playerActive ? 'ignore' : 'keep-paused';
  return 'adopt';
}
