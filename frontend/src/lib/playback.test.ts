import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  indexOfUri,
  observeSpeaker,
  remainingUris,
  startHasLanded,
  trackHasEnded,
  type TrackRef,
} from './playback.ts';

const queue: TrackRef[] = [
  { uri: 'spotify:track:aaa' },
  { uri: 'spotify:track:bbb' },
  { uri: 'spotify:track:ccc' },
];

describe('remainingUris', () => {
  it('sends the rest of the queue from the playing index', () => {
    assert.deepEqual(remainingUris(queue, 1), ['spotify:track:bbb', 'spotify:track:ccc']);
  });

  it('drops non-track uris', () => {
    assert.deepEqual(remainingUris([{ uri: 'spotify:episode:x' }, { uri: 'spotify:track:aaa' }], 0), [
      'spotify:track:aaa',
    ]);
  });

  it('is empty when the index is past the queue', () => {
    assert.deepEqual(remainingUris(queue, 9), []);
  });
});

describe('observeSpeaker', () => {
  it('treats iPhone pause of the current track as pause, not as “play the next song”', () => {
    const result = observeSpeaker({
      queue,
      activeIndex: 0,
      assumedPlaying: true,
      startingUri: '',
      speaker: { uri: 'spotify:track:aaa', isPlaying: false },
    });
    assert.deepEqual(result, { type: 'paused' });
  });

  it('follows Spotify when the speaker itself advanced to the next queued uri', () => {
    const result = observeSpeaker({
      queue,
      activeIndex: 0,
      assumedPlaying: true,
      startingUri: '',
      speaker: { uri: 'spotify:track:bbb', isPlaying: true },
    });
    assert.deepEqual(result, { type: 'follow', index: 1 });
  });

  it('goes idle when the speaker session ended (no current item)', () => {
    const result = observeSpeaker({
      queue,
      activeIndex: 2,
      assumedPlaying: true,
      startingUri: '',
      speaker: null,
    });
    assert.deepEqual(result, { type: 'idle' });
  });

  it('does not start anything when already paused and the speaker is still paused', () => {
    const result = observeSpeaker({
      queue,
      activeIndex: 0,
      assumedPlaying: false,
      startingUri: '',
      speaker: { uri: 'spotify:track:aaa', isPlaying: false },
    });
    assert.deepEqual(result, { type: 'ignore' });
  });

  it('ignores a foreign track the iPhone started', () => {
    const result = observeSpeaker({
      queue,
      activeIndex: 0,
      assumedPlaying: true,
      startingUri: '',
      speaker: { uri: 'spotify:track:zzz', isPlaying: true },
    });
    assert.deepEqual(result, { type: 'ignore' });
  });

  it('does not treat the previous track as a pause while a new start is in flight', () => {
    const result = observeSpeaker({
      queue,
      activeIndex: 1,
      assumedPlaying: true,
      startingUri: 'spotify:track:bbb',
      speaker: { uri: 'spotify:track:aaa', isPlaying: true },
    });
    assert.deepEqual(result, { type: 'ignore' });
  });

  it('follows once the started uri is actually playing', () => {
    assert.equal(
      startHasLanded('spotify:track:bbb', { uri: 'spotify:track:bbb', isPlaying: true }),
      true,
    );
    const result = observeSpeaker({
      queue,
      activeIndex: 0,
      assumedPlaying: true,
      startingUri: 'spotify:track:bbb',
      speaker: { uri: 'spotify:track:bbb', isPlaying: true },
    });
    assert.deepEqual(result, { type: 'follow', index: 1 });
  });

  it('pauses and moves the card if the speaker is paused on another queued track', () => {
    const result = observeSpeaker({
      queue,
      activeIndex: 0,
      assumedPlaying: true,
      startingUri: '',
      speaker: { uri: 'spotify:track:bbb', isPlaying: false },
    });
    assert.deepEqual(result, { type: 'paused', index: 1 });
  });

  it('treats a stop near the end of the current track as ended, not pause', () => {
    const result = observeSpeaker({
      queue,
      activeIndex: 0,
      assumedPlaying: true,
      startingUri: '',
      speaker: { uri: 'spotify:track:aaa', isPlaying: false, progressMs: 179500, durationMs: 180000 },
    });
    assert.deepEqual(result, { type: 'ended' });
  });
});

describe('trackHasEnded', () => {
  it('is false while the speaker is still playing', () => {
    assert.equal(trackHasEnded({ uri: 'spotify:track:aaa', isPlaying: true, progressMs: 180000, durationMs: 180000 }), false);
  });

  it('is true when progress is within two seconds of duration', () => {
    assert.equal(trackHasEnded({ uri: 'spotify:track:aaa', isPlaying: false, progressMs: 178000, durationMs: 180000 }), true);
  });
});

describe('indexOfUri', () => {
  it('finds a queued uri', () => {
    assert.equal(indexOfUri(queue, 'spotify:track:ccc'), 2);
  });
});
