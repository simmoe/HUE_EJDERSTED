<script lang="ts">
  import { onMount } from 'svelte';
  import { fade } from 'svelte/transition';
  import { hsvToHex, store, type AudioTargetStatus } from '$lib/ws.svelte';
  import Card from '$lib/Card.svelte';
  import VolumeKnob from '$lib/VolumeKnob.svelte';
  import SpotifyVoice from '$lib/SpotifyVoice.svelte';
  import FeedbackOverlay from '$lib/FeedbackOverlay.svelte';
  import CameraCard from '$lib/CameraCard.svelte';
  import { showFeedback } from '$lib/feedback.svelte';
  import {
    radioLibrary,
    initRadioLibrary,
    saveRadioPlaylist,
    saveTrackToSavedSongs,
    SAVED_SONGS_PLAYLIST_NAME,
    deleteRadioPlaylist,
    deleteRadioTrack,
    type RadioPlaylist,
  } from '$lib/radioLibrary.svelte';
  import {
    playlist,
    activeQueue,
    registerScrollToNowPlaying,
    initPlaylistHub,
    togglePlayPause,
    spotifyNextTrack,
    spotifyPreviousTrack,
    togglePlaylistContext,
    isPlaylistContextActive,
    playAlbum,
    handleVoicePayload,
    releaseSpotifyForPodcast,
    releasePodcastForMusic,
    registerPodcastReleaseHandler,
    paintNpFromQueues,
    playFromCurrentIndex,
    playExactTrack,
    setPodcastTransportFromPlayer,
    clearPodcastTransport,
    claimMusicSession,
    claimPodcastSession,
  } from '$lib/playlistHub.svelte';
  import { init as initSpotifyWebPlayer } from '$lib/spotifyPlayer.svelte';
  import { nowPlayingFromSession, podcastPollAction, resolvePlaylistTap } from '$lib/playbackSession';

  const enabled = (feature: keyof typeof store.config.features) => !!store.config.features[feature];

  // ── Wake lock (hold skærm tændt) ───────────────────────────────────────────
  let wakeLock: WakeLockSentinel | null = null;

  async function requestWakeLock() {
    try {
      if ('wakeLock' in navigator) {
        wakeLock = await navigator.wakeLock.request('screen');
        wakeLock.addEventListener('release', () => { wakeLock = null; });
      }
    } catch {}
  }

  // ── Auto-dim: dæmp skærmen efter 30s inaktivitet ───────────────────────────
  const CLOCK_IDLE_DELAY_MS = 30_000;
  let dimmed = $state(false);
  let idleInterval: ReturnType<typeof setInterval>;
  let lastActivityAt = Date.now();

  async function setBrightness(level: number) {
    try { await fetch(`/api/brightness/${level}`, { method: 'PUT' }); } catch {}
  }

  function lockLandscape() {
    const orientation = screen.orientation as ScreenOrientation & {
      lock?: (orientation: 'landscape') => Promise<void>;
    };
    orientation?.lock?.('landscape').catch(() => {});
  }

  function requestFullscreenAndKiosk() {
    document.documentElement.requestFullscreen?.()
      .then(lockLandscape)
      .catch(lockLandscape);
    setTimeout(lockLandscape, 250);
    requestWakeLock();
    if (enabled('adbKiosk')) fetch('/api/kiosk', { method: 'POST' }).catch(() => {});
  }

  function resetDim(_wakeKiosk = false) {
    if (showSplash) return;
    lastActivityAt = Date.now();
    if (!dimmed) return;
    requestFullscreenAndKiosk();
    setBrightness(255);
    dimmed = false;
  }

  function noteActivity(wakeKiosk = false) {
    resetDim(wakeKiosk);
  }

  function updateIdleState() {
    if (showSplash || dimmed || Date.now() - lastActivityAt < CLOCK_IDLE_DELAY_MS) return;
    dimmed = true;
    setBrightness(60);
  }

  // ── Clock ──────────────────────────────────────────────────────────────────
  let clockTime = $state('');
  let clockInterval: ReturnType<typeof setInterval>;
  let podcastPlayerInterval: ReturnType<typeof setInterval>;
  let audioTargetInterval: ReturnType<typeof setInterval>;

  function updateClock() {
    const now = new Date();
    clockTime = now.toLocaleTimeString('da-DK', { hour: '2-digit', minute: '2-digit', hour12: false });
  }

  // ── Fullscreen splash ───────────────────────────────────────────────────────
  let showSplash = $state(true);

  function dismissSplash() {
    requestFullscreenAndKiosk();
    showSplash = false;
    resetDim();
    // Efter brugertryk: Web Playback SDK må bruge audio; Chrome bliver Connect-enhed «Ejdersted».
    // Ikke i garden — der spiller Pi'ens librespot, ikke browseren.
    if (enabled('spotify') && !isGarden()) void initSpotifyWebPlayer();
  }

  let stopPlaylistHub: (() => void) | undefined;
  let stopRadioLibrary: (() => void) | undefined;
  let audioTargets = $state<AudioTargetStatus[]>([]);
  let connectingAudioTarget = $state('');

  async function refreshAudioTargets() {
    if (!enabled('audio')) return;
    try {
      const next = await store.getAudioTargets();
      const onlineVolumes: Record<string, number> = {};
      for (const target of next) {
        if (target.online && typeof target.volume === 'number') {
          onlineVolumes[target.id] = target.volume;
        }
      }
      audioTargetVolumes = onlineVolumes;
      audioTargets = next;
    } catch {
      audioTargets = [];
      audioTargetVolumes = {};
    }
  }

  async function reconnectAudioTarget(targetId: string) {
    if (connectingAudioTarget) return;
    connectingAudioTarget = targetId;
    try {
      const result = await store.connectAudioTarget(targetId);
      audioTargets = [
        result,
        ...audioTargets.filter((target) => target.id !== targetId),
      ].sort((a, b) => Number(b.default) - Number(a.default));
      showFeedback(result.online ? `forbundet: ${result.name}` : (result.error ?? 'kunne ikke forbinde'), {
        kind: result.online ? 'success' : 'error',
        duration: 6000,
      });
    } catch {
      showFeedback('kunne ikke forbinde højttaler', { kind: 'error', duration: 6000 });
    } finally {
      connectingAudioTarget = '';
    }
  }

  onMount(() => {
    store.connect();
    updateClock();
    clockInterval = setInterval(updateClock, 1000);
    idleInterval = setInterval(updateIdleState, 500);
    void fetch('/api/config')
      .then((r) => r.json())
      .then((cfg) => {
        store.config = cfg;
        // Garden routes Spotify through the on-Pi librespot device, so the kiosk
        // must NOT become a Web Playback (Connect) endpoint itself.
        if (enabled('spotify') && !isGarden()) void initSpotifyWebPlayer();
        if (enabled('spotify')) {
          void initPlaylistHub().then((stop) => {
            stopPlaylistHub = stop;
          });
        }
        if (enabled('playlists')) {
          void initRadioLibrary().then((stop) => {
            stopRadioLibrary = stop;
          });
        }
        void refreshAudioTargets();
        if (enabled('audio')) {
          audioTargetInterval = setInterval(() => {
            void refreshAudioTargets();
          }, 10_000);
        }
        if (enabled('podcasts')) {
          void loadPodcasts();
          void refreshPodcastPlayer(false);
          podcastPlayerInterval = setInterval(() => {
            void refreshPodcastPlayer();
          }, 1500);
        }
        registerPodcastReleaseHandler(() => {
          activePodcastPlayer = { ...activePodcastPlayer, active: false, playing: false };
        });
      })
      .catch(() => {
        if (enabled('spotify')) void initSpotifyWebPlayer();
      });
    // Re-apply kiosk settings once on page load. Do not run it on every visibility
    // change; Android may briefly hide/show Chrome around system overlays.
    setTimeout(() => {
      if (enabled('adbKiosk')) fetch('/api/kiosk', { method: 'POST' }).catch(() => {});
    }, 1500);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        requestWakeLock();
        lockLandscape();
        if (enabled('spotify') && !isGarden()) void initSpotifyWebPlayer();
      }
    });
    const onDirectActivity = () => noteActivity(true);
    const onContinuousActivity = () => noteActivity(false);
    // Keep auto-dim tied to real UI activity. Avoid keydown: the case can hold volume buttons.
    document.addEventListener('pointerdown', onDirectActivity, { passive: true });
    document.addEventListener('pointermove', onContinuousActivity, { passive: true });
    document.addEventListener('click', onDirectActivity, { passive: true });
    document.addEventListener('input', onContinuousActivity, { passive: true });
    document.addEventListener('change', onContinuousActivity, { passive: true });
    document.addEventListener('focusin', onDirectActivity, { passive: true });
    document.addEventListener('scroll', onContinuousActivity, { passive: true, capture: true });
    return () => {
      clearInterval(clockInterval);
      clearInterval(idleInterval);
      clearInterval(audioTargetInterval);
      clearInterval(podcastPlayerInterval);
      document.removeEventListener('pointerdown', onDirectActivity);
      document.removeEventListener('pointermove', onContinuousActivity);
      document.removeEventListener('click', onDirectActivity);
      document.removeEventListener('input', onContinuousActivity);
      document.removeEventListener('change', onContinuousActivity);
      document.removeEventListener('focusin', onDirectActivity);
      document.removeEventListener('scroll', onContinuousActivity, { capture: true });
      stopPlaylistHub?.();
      stopRadioLibrary?.();
    };
  });

  // ── Horizontal page carousel ────────────────────────────────────────────────
  let pagesEl: HTMLDivElement;
  let advancing = $state(false);
  let nextPageName = $state('');

  function readNextPageName() {
    const el = pagesEl?.children[2] as HTMLElement | undefined
      ?? pagesEl?.children[1] as HTMLElement | undefined;
    nextPageName = el?.querySelector('.col-header')?.textContent ?? '';
  }

  function advance() {
    if (advancing || !pagesEl || pagesEl.children.length < 2) return;
    advancing = true;
    const pageW = pagesEl.clientWidth / 2;  // each page = 50%
    pagesEl.scrollTo({ left: pageW, behavior: 'smooth' });

    // When scroll finishes: move first page to end, reset scroll instantly
    function onDone() {
      pagesEl.removeEventListener('scrollend', onDone);
      const first = pagesEl.firstElementChild;
      if (first && pagesEl.children.length > 1) pagesEl.appendChild(first);
      pagesEl.scrollTo({ left: 0, behavior: 'instant' });
      advancing = false;
      readNextPageName();
    }
    pagesEl.addEventListener('scrollend', onDone, { once: true });
    // Fallback if scrollend doesn't fire (older browsers)
    setTimeout(() => { if (advancing) onDone(); }, 600);
  }

  // ── Lyd: mute (ét mute-niveau per enhed) ───────────────────────────────────
  let muteState = $state<Record<string, { muted: boolean; prev: number }>>({});

  // ── Song-change pulse + streamer ───────────────────────────────────────────
  let pulsingDevices = $state<Record<string, boolean>>({});
  let lastSong = $state<Record<string, string>>({});
  let streamer = $state<{ title: string; artist: string } | null>(null);
  let streamerTimer: ReturnType<typeof setTimeout>;

  $effect(() => {
    if (playlist.spotifyTitle) checkSaved();
  });

  $effect(() => {
    void playlist.radioQueue;
    void playlist.playListMode;
    radioSaveDone = false;
  });

  $effect(() => {
    if (!drilledPlaylist) return;
    const live = radioLibrary.playlists.find((p) => p.id === drilledPlaylist?.id);
    if (!live) return;
    drilledPlaylist = live;
    drilledTracks = live.tracks.map((track, i) => ({ ...track, position: i }));
  });

  $effect(() => {
    for (const [id, np] of Object.entries(store.nowPlaying)) {
      const key = `${np.name}\u2014${np.artist}`;
      if (lastSong[id] && lastSong[id] !== key && np.name) {
        pulsingDevices = { ...pulsingDevices, [id]: true };
        setTimeout(() => {
          pulsingDevices = { ...pulsingDevices, [id]: false };
        }, 1200);

        // Streamer kun når skærmen er dæmpet
        if (dimmed) {
          clearTimeout(streamerTimer);
          streamer = { title: np.name, artist: np.artist };
          streamerTimer = setTimeout(() => { streamer = null; }, 6000);
        }
      }
      lastSong[id] = key;
    }
  });

  function toggleMute(deviceId: string, currentLevel: number) {
    const m = muteState[deviceId];
    if (m?.muted) {
      muteState[deviceId] = { muted: false, prev: m.prev };
      store.setVolume(deviceId, m.prev || 20);
    } else {
      muteState[deviceId] = { muted: true, prev: currentLevel };
      store.setVolume(deviceId, 0);
    }
  }

  // ── Samlet volumen ──────────────────────────────────────────────────────────
  // Home: én slider styrer alle online B&O-højttalere.
  // Garden: samme slider styrer den forbundne BlueALSA-output-target.
  let unifiedVolume = $state(40);
  let unifiedDragging = false;
  let targetVolTimer: ReturnType<typeof setTimeout> | null = null;
  let audioTargetVolumes = $state<Record<string, number>>({});

  const isGarden = () => store.config.site === 'garden';

  function gardenAudioTargetId(): string {
    return store.config.audio?.defaultTarget || audioTargets[0]?.id || '';
  }

  $effect(() => {
    if (unifiedDragging) return;
    const nextTargetVolumes: Record<string, number> = {};
    for (const target of audioTargets) {
      if (typeof target.volume === 'number') nextTargetVolumes[target.id] = target.volume;
    }
    if (Object.entries(nextTargetVolumes).some(([id, volume]) => audioTargetVolumes[id] !== volume)) {
      audioTargetVolumes = { ...audioTargetVolumes, ...nextTargetVolumes };
    }
    if (isGarden()) {
      const id = gardenAudioTargetId();
      const targetVolume = audioTargetVolumes[id] ?? audioTargets.find((t) => t.id === id)?.volume;
      if (typeof targetVolume === 'number') unifiedVolume = targetVolume;
      return;
    }
    const online = store.devices
      .map((d) => store.volumes[d.id])
      .filter((v) => v?.online);
    if (online.length === 0) return;
    const avg = Math.round(online.reduce((s, v) => s + v.level, 0) / online.length);
    unifiedVolume = avg;
  });

  function readTargetVolume(target: AudioTargetStatus): number {
    if (!target.online) return 0;
    return audioTargetVolumes[target.id] ?? target.volume ?? unifiedVolume;
  }

  function writeLocalTargetVolume(targetId: string, level: number) {
    audioTargetVolumes = { ...audioTargetVolumes, [targetId]: level };
    audioTargets = audioTargets.map((target) =>
      target.id === targetId ? { ...target, volume: level } : target
    );
  }

  function queueTargetVolume(targetId: string, level: number) {
    writeLocalTargetVolume(targetId, level);
    if (targetVolTimer) clearTimeout(targetVolTimer);
    targetVolTimer = setTimeout(() => {
      targetVolTimer = null;
      void store.setAudioTargetVolume(targetId, level).then((result) => {
        if (result.ok && typeof result.volume === 'number') {
          writeLocalTargetVolume(targetId, result.volume);
          if (targetId === gardenAudioTargetId()) unifiedVolume = result.volume;
        }
      });
    }, 120);
  }

  function setUnifiedVolume(level: number) {
    unifiedVolume = level;
    if (isGarden()) {
      const id = gardenAudioTargetId();
      if (id) queueTargetVolume(id, level);
      return;
    }
    for (const d of store.devices) {
      if (store.volumes[d.id]?.online) {
        store.setVolume(d.id, level);
        if (muteState[d.id]?.muted && level > 0) {
          muteState[d.id] = { muted: false, prev: muteState[d.id].prev };
        }
      }
    }
  }

  // ── Lys: Hue pairing ────────────────────────────────────────────────────────
  let hueMuteState = $state<Record<string, { muted: boolean; prev: number }>>({});

  function toggleHueMute(roomId: string, currentBrightness: number) {
    const room = store.hueRooms.find(r => r.id === roomId);
    if (room && !room.any_on) {
      // Light is off — turn on with stored brightness
      store.setHueBrightness(roomId, currentBrightness || 50);
      hueMuteState[roomId] = { muted: false, prev: currentBrightness || 50 };
      return;
    }
    const m = hueMuteState[roomId];
    if (m?.muted) {
      hueMuteState[roomId] = { muted: false, prev: m.prev };
      store.setHueBrightness(roomId, m.prev || 50);
    } else {
      hueMuteState[roomId] = { muted: true, prev: currentBrightness };
      store.setHueBrightness(roomId, 0);
    }
  }

  function lightStatus(light: { id: string; on: boolean; online: boolean; error?: string }): string {
    if (connectingLight === light.id) return 'forbinder';
    if (light.error === 'ikke parret') return 'ikke parret';
    if (light.error === 'mangler nøgle') return 'mangler nøgle';
    if (!light.online) return 'offline';
    return light.on ? 'tændt' : 'slukket';
  }

  async function reconnectLight(lightId: string) {
    if (connectingLight) return;
    connectingLight = lightId;
    try {
      const result = await store.connectLight(lightId);
      showFeedback(result?.online ? 'forbundet' : (result?.error ?? 'kunne ikke forbinde'), {
        kind: result?.online ? 'success' : 'error',
        duration: 5000,
      });
    } catch {
      showFeedback('kunne ikke forbinde', { kind: 'error', duration: 5000 });
    } finally {
      connectingLight = '';
    }
  }

  let connectingLight = $state('');

  const lightColorPresets = [
    { id: 'warm', hue: 32, sat: 48, label: 'varm' },
    { id: 'amber', hue: 38, sat: 80, label: 'amber' },
    { id: 'red', hue: 0, sat: 85, label: 'rød' },
  ];

  let spotifySaved = $state(false);
  let saveLoading = $state(false);
  let radioSaveDone = $state(false);

  // ── Vertical card carousel ──────────────────────────────────────────────
  let lydInner = $state<HTMLDivElement>();
  let lysInner = $state<HTMLDivElement>();
  let cardAdvancing = $state(false);
  let nextLydCard = $state('');
  let nextLysCard = $state('');

  function readNextCardName(el: HTMLDivElement): string {
    const child = el?.children[1] as HTMLElement | undefined;
    if (!child) return '';
    return child.querySelector('.card-name')?.textContent ?? child.dataset.name ?? '';
  }

  function advanceCard(el: HTMLDivElement, kind: 'lyd' | 'lys' | 'podcast' | 'playlist') {
    if (cardAdvancing || !el || el.children.length < 2) return;
    if (kind === 'playlist') {
      scrollPlaylistPage(1);
      return;
    }
    cardAdvancing = true;
    const cardH = el.clientHeight;
    el.scrollTo({ top: cardH, behavior: 'smooth' });

    function onDone() {
      el.removeEventListener('scrollend', onDone);
      const first = el.firstElementChild;
      if (first) el.appendChild(first);
      el.scrollTo({ top: 0, behavior: 'instant' });
      cardAdvancing = false;
      if (kind === 'lyd') nextLydCard = readNextCardName(el);
      else if (kind === 'lys') nextLysCard = readNextCardName(el);
      else if (kind === 'podcast') nextPodcastCard = readNextCardName(el);
      else nextPlaylistCard = readNextCardName(el);
    }
    el.addEventListener('scrollend', onDone, { once: true });
    setTimeout(() => { if (cardAdvancing) onDone(); }, 600);
  }

  // Read initial next-names once DOM is ready
  $effect(() => {
    if (pagesEl) readNextPageName();
    if (lydInner) nextLydCard = readNextCardName(lydInner);
    if (lysInner) nextLysCard = readNextCardName(lysInner);
    // re-read når podcasts er hentet (dom-børn ændrer sig)
    void podcasts.length;
    if (podcastInner) nextPodcastCard = readNextCardName(podcastInner);
    void radioLibrary.playlists.length;
    if (playlistInner) nextPlaylistCard = readNextCardName(playlistInner);
  });

  $effect(() => {
    if (!lydInner) return;
    registerScrollToNowPlaying(() => {
      if (!lydInner) return;
      const lydPage = lydInner.closest('.page') as HTMLElement | null;
      if (pagesEl && lydPage) {
        pagesEl.scrollTo({ left: lydPage.offsetLeft, behavior: 'smooth' });
        setTimeout(readNextPageName, 350);
      }
      while (lydInner.firstElementChild && !lydInner.firstElementChild.classList.contains('np-card')) {
        lydInner.appendChild(lydInner.firstElementChild);
      }
      lydInner.scrollTo({ top: 0, behavior: 'instant' });
    });
  });

  function checkSaved() {
    spotifySaved = isCurrentTrackSaved();
  }

  function currentDisplayedTrack() {
    if (!playlist.spotifyTrackUri?.startsWith('spotify:track:')) return null;
    return {
      uri: playlist.spotifyTrackUri,
      name: playlist.spotifyTitle,
      artist: playlist.spotifyArtist,
    };
  }

  function isCurrentTrackSaved() {
    const uri = playlist.spotifyTrackUri;
    if (!uri) return false;
    return radioLibrary.playlists.some((p) => p.name === SAVED_SONGS_PLAYLIST_NAME && p.tracks.some((t) => t.uri === uri));
  }

  // A radio playlist is saveable whenever we're in the radio context with a
  // multi-track queue — independent of the transient spotifyRadio *playback*
  // flag, which finishActiveQueuePlayback() clears when the clock elapses.
  function isRadioPlaylistSaveable() {
    return playlist.playListMode === 'radio' && playlist.radioQueue.length > 1;
  }

  function currentSaveLabel() {
    if (isRadioPlaylistSaveable()) return radioSaveDone ? 'PLAYLISTE GEMT' : 'GEM PLAYLISTE';
    if (playlist.playListMode === 'playlist' && playlist.savedPlaylistActive) return 'PLAYLISTE GEMT';
    return isCurrentTrackSaved() ? 'GEMT' : 'GEM';
  }

  async function saveCurrentSelection() {
    if (saveLoading || !playlist.spotifyTrackUri) return;
    saveLoading = true;
    try {
      if (isRadioPlaylistSaveable()) {
        const seed = playlist.radioQueue[0];
        const saved = await saveRadioPlaylist(seed, playlist.radioQueue);
        radioSaveDone = true;
        showFeedback(`Playliste gemt som "${saved.name}" (${saved.tracks.length} sange)`, { kind: 'success', duration: 7000 });
        return;
      }
      const track = currentDisplayedTrack();
      if (!track) return;
      const result = await saveTrackToSavedSongs(track);
      spotifySaved = true;
      showFeedback(
        result.added
          ? `"${track.name}" er gemt i "${SAVED_SONGS_PLAYLIST_NAME}"`
          : `"${track.name}" ligger allerede i "${SAVED_SONGS_PLAYLIST_NAME}"`,
        { kind: 'success', duration: 7000 },
      );
    } catch (e) {
      showFeedback((e as Error).message || 'Kunne ikke gemme', { kind: 'error' });
    } finally {
      saveLoading = false;
    }
  }

  let hueManualIp  = $state('');
  let huePairing   = $state(false);

  async function handlePair(e: Event) {
    e.preventDefault();
    huePairing = true;
    const err = await store.pairHue(hueManualIp.trim() || undefined);
    huePairing = false;
    if (err) {
      showFeedback(err, { kind: 'error' });
    } else {
      hueManualIp = '';
      showFeedback('Hue bridge parret', { kind: 'success' });
    }
  }

  // ── Podcasts ─────────────────────────────────────────────────────────────
  type Podcast = {
    show_id: string;
    show_name: string;
    show_image: string;
    episode_id: string;
    episode_uri: string;
    episode_name: string;
    episode_release_date: string;
    episode_duration_ms: number;
  };

  type Episode = {
    id: string;
    uri: string;
    name: string;
    release_date: string;
    duration_ms: number;
  };

  type PodcastPlayerState = {
    active: boolean;
    source: string;
    showId: string;
    showTitle: string;
    episodeId: string;
    episodeUri: string;
    episodeTitle: string;
    episodeIndex: number;
    queue: Episode[];
    playing: boolean;
    positionMs: number;
    durationMs: number;
    updatedAt: number;
    error?: string;
  };

  const emptyPodcastPlayer: PodcastPlayerState = {
    active: false,
    source: '',
    showId: '',
    showTitle: '',
    episodeId: '',
    episodeUri: '',
    episodeTitle: '',
    episodeIndex: 0,
    queue: [],
    playing: false,
    positionMs: 0,
    durationMs: 0,
    updatedAt: 0,
  };

  let podcasts = $state<Podcast[]>([]);
  let podcastsLoading = $state(true);
  let podcastsError = $state('');
  let activePodcastPlayer = $state<PodcastPlayerState>({ ...emptyPodcastPlayer });
  let seekingPodcast = $state(false);
  let podcastSeekOpen = $state(false);
  let activePodcastId = $state('');
  let activeEpisodeId = $state('');
  let loadingPodcastId = $state('');
  let loadingEpisodeId = $state('');
  let podcastInner = $state<HTMLDivElement>();
  let nextPodcastCard = $state('');
  let prevPodcastCard = $state('');
  let showPodcastQueue = $state(false);

  function updatePodcastScrollLabels() {
    if (!podcastInner) return;
    const rows = [...podcastInner.querySelectorAll<HTMLElement>('.podcast-card')];
    if (!rows.length) { nextPodcastCard = ''; prevPodcastCard = ''; return; }
    const currentTop = podcastInner.scrollTop;
    const next = rows.find((row) => row.offsetTop > currentTop + 12);
    const previous = [...rows].reverse().find((row) => row.offsetTop < currentTop - 12);
    nextPodcastCard = next?.dataset.name ?? '';
    prevPodcastCard = previous?.dataset.name ?? '';
  }

  function scrollPodcastPage(direction: 1 | -1) {
    if (!podcastInner) return;
    const rows = [...podcastInner.querySelectorAll<HTMLElement>('.podcast-card')];
    if (!rows.length) return;
    const currentTop = podcastInner.scrollTop;
    const target = direction > 0
      ? rows.find((row) => row.offsetTop > currentTop + 12)
      : [...rows].reverse().find((row) => row.offsetTop < currentTop - 12);
    if (!target) {
      podcastInner.scrollTo({ top: direction > 0 ? podcastInner.scrollHeight : 0, behavior: 'smooth' });
      setTimeout(updatePodcastScrollLabels, 350);
      return;
    }
    podcastInner.scrollTo({ top: target.offsetTop, behavior: 'smooth' });
    setTimeout(updatePodcastScrollLabels, 350);
  }

  function openPodcastSeek() {
    if (!isPodcastTransport()) return;
    podcastSeekOpen = true;
  }

  function closePodcastSeek() {
    podcastSeekOpen = false;
    seekingPodcast = false;
  }

  function goToPodcastNow() {
    closePodcastSeek();
    openPodcastQueue();
    if (!pagesEl) return;
    for (let i = 0; i < pagesEl.children.length; i++) {
      const first = pagesEl.firstElementChild as HTMLElement | null;
      if (first?.dataset.page === 'podcast') break;
      if (first) pagesEl.appendChild(first);
    }
    pagesEl.scrollTo({ left: 0, behavior: 'instant' });
    readNextPageName();
  }

  function openPodcastQueue() {
    if (!activePodcastPlayer.active && playlist.podcastQueue.length === 0) return;
    closeDrill();
    showPodcastQueue = true;
    requestAnimationFrame(() => {
      podcastInner?.scrollTo({ top: 0, behavior: 'smooth' });
      updatePodcastScrollLabels();
    });
  }

  function closePodcastQueue() {
    showPodcastQueue = false;
    requestAnimationFrame(updatePodcastScrollLabels);
  }

  // ── Drill-in state (per show, holdt indenfor podcast-kolonnen) ────────────
  let drilledShow = $state<Podcast | null>(null);
  let drilledEpisodes = $state<Episode[]>([]);
  let drilledLoading = $state(false);
  let drilledError = $state('');
  let drilledHasMore = $state(false);
  let drilledLoadingMore = $state(false);
  const EPISODE_PAGE_SIZE = 20;

  function normalizePodcastPlayer(player: Partial<PodcastPlayerState> | undefined): PodcastPlayerState {
    if (!player) return { ...emptyPodcastPlayer };
    const queue = Array.isArray(player.queue) ? player.queue : [];
    return {
      ...emptyPodcastPlayer,
      ...player,
      active: !!player.active,
      queue: queue.map((ep) => ({
        id: String(ep.id ?? ''),
        uri: String(ep.uri ?? ''),
        name: String(ep.name ?? ''),
        release_date: String(ep.release_date ?? ''),
        duration_ms: Number(ep.duration_ms ?? 0),
      })),
      episodeIndex: Number(player.episodeIndex ?? 0),
      positionMs: Number(player.positionMs ?? 0),
      durationMs: Number(player.durationMs ?? 0),
      updatedAt: Number(player.updatedAt ?? Date.now()),
      playing: !!player.playing,
    };
  }

  function adoptPodcastPlayer(
    player: Partial<PodcastPlayerState> | undefined,
    push = true,
    userInitiated = false,
  ) {
    const next = normalizePodcastPlayer(player);
    const action = podcastPollAction(playlist.activeTransport, next.active, userInitiated);
    if (action === 'ignore') return;
    if (action === 'keep-paused') {
      activePodcastPlayer = { ...activePodcastPlayer, active: false, playing: false };
      return;
    }
    if (next.active) {
      activePodcastPlayer = next;
      activePodcastId = next.showId;
      activeEpisodeId = next.episodeId;
      setPodcastTransportFromPlayer(next as unknown as Record<string, unknown>, push);
      return;
    }
    const wasPodcast = activePodcastPlayer.active || playlist.activeTransport === 'podcast';
    activePodcastPlayer = next;
    if (wasPodcast && playlist.activeTransport === 'podcast') {
      activePodcastId = '';
      activeEpisodeId = '';
      clearPodcastTransport(push);
    }
  }

  async function refreshPodcastPlayer(push = true) {
    if (!enabled('podcasts')) return;
    try {
      const r = await fetch('/api/podcasts/player');
      const data = await r.json();
      if (seekingPodcast) return;
      if (data?.ok && data.player) adoptPodcastPlayer(data.player as Partial<PodcastPlayerState>, push);
    } catch {
      /* backend kan være midt i restart */
    }
  }

  async function postPodcastControl(path: string, body?: Record<string, unknown>) {
    const r = await fetch(`/api/podcasts/player/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json();
    if (data?.player) adoptPodcastPlayer(data.player as Partial<PodcastPlayerState>, true, true);
    if (!data?.ok) {
      const detail = String(data?.error || data?.detail || '').trim();
      if (detail) showFeedback(detail, { kind: 'error' });
    }
    return !!data?.ok;
  }

  async function togglePodcastPlayPause() {
    if (!activePodcastPlayer.active) return;
    await postPodcastControl(activePodcastPlayer.playing ? 'pause' : 'resume');
  }

  async function clearPodcastQueue() {
    await postPodcastControl('clear');
  }

  async function seekPodcast(offsetSeconds: number) {
    await postPodcastControl('seek', { offsetSeconds });
  }

  async function seekPodcastTo(positionMs: number) {
    seekingPodcast = false;
    await postPodcastControl('seek', { positionSeconds: Math.max(0, positionMs) / 1000 });
  }

  async function podcastNext() {
    await postPodcastControl('next');
  }

  async function podcastPrevious() {
    await postPodcastControl('previous');
  }

  async function loadPodcasts(refresh = false) {
    podcastsLoading = podcasts.length === 0;
    podcastsError = '';
    try {
      const r = await fetch(`/api/podcasts${refresh ? '?refresh=1' : ''}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as Podcast[];
      podcasts = Array.isArray(data) ? data : [];
    } catch (e) {
      podcastsError = (e as Error).message || 'Kunne ikke hente podcasts';
    } finally {
      podcastsLoading = false;
    }
  }

  async function playPodcast(showId: string) {
    if (loadingPodcastId) return;
    if (activePodcastId === showId && playlist.activeTransport === 'podcast') {
      await togglePodcastPlayPause();
      return;
    }
    loadingPodcastId = showId;
    try {
      claimPodcastSession();
      await releaseSpotifyForPodcast();
      const ctrl = new AbortController();
      const timeout = setTimeout(() => ctrl.abort(), 25_000);
      const r = await fetch('/api/podcasts/play-latest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ show_id: showId }),
        signal: ctrl.signal,
      }).finally(() => clearTimeout(timeout));
      const data = await r.json();
      if (data.ok) {
        if (data.player) adoptPodcastPlayer(data.player as Partial<PodcastPlayerState>, true, true);
        else {
          activePodcastId = showId;
          activeEpisodeId = (data.episode?.id as string) || '';
        }
        openPodcastQueue();
      } else {
        const detail = String(data.detail || data.error || '').trim();
        if (detail) showFeedback(detail, { kind: 'error' });
      }
    } catch (e) {
      showFeedback((e as Error).message || 'POST /api/podcasts/play-latest fejlede', { kind: 'error' });
    } finally {
      loadingPodcastId = '';
    }
  }

  async function openDrill(show: Podcast) {
    drilledShow = show;
    drilledEpisodes = [];
    drilledHasMore = false;
    drilledError = '';
    drilledLoading = true;
    try {
      const r = await fetch(`/api/podcasts/${encodeURIComponent(show.show_id)}/episodes?limit=${EPISODE_PAGE_SIZE}&offset=0`);
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        drilledError = (data as { error?: string; detail?: string }).detail
          || (data as { error?: string }).error
          || `GET /api/podcasts/${show.show_id}/episodes HTTP ${r.status}`;
        return;
      }
      drilledEpisodes = (data.episodes ?? []) as Episode[];
      drilledHasMore = !!data.has_more;
      if (drilledEpisodes.length === 0) {
        drilledError = 'Ingen afsnit fundet.';
      }
    } catch (e) {
      drilledError = (e as Error).message || 'GET /api/podcasts/.../episodes fejlede';
    } finally {
      drilledLoading = false;
    }
  }

  function closeDrill() {
    drilledShow = null;
    drilledEpisodes = [];
    drilledHasMore = false;
    drilledError = '';
  }

  async function loadMoreEpisodes() {
    if (!drilledShow || drilledLoadingMore || !drilledHasMore) return;
    drilledLoadingMore = true;
    try {
      const offset = drilledEpisodes.length;
      const r = await fetch(`/api/podcasts/${encodeURIComponent(drilledShow.show_id)}/episodes?limit=${EPISODE_PAGE_SIZE}&offset=${offset}`);
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = String((data as { detail?: string; error?: string }).detail || (data as { error?: string }).error || '').trim();
        if (detail) showFeedback(detail, { kind: 'error' });
        return;
      }
      const more = (data.episodes ?? []) as Episode[];
      drilledEpisodes = [...drilledEpisodes, ...more];
      drilledHasMore = !!data.has_more;
    } catch {
      /* */
    } finally {
      drilledLoadingMore = false;
    }
  }

  async function playEpisode(ep: Episode) {
    if (loadingEpisodeId) return;
    if (activeEpisodeId === ep.id && playlist.activeTransport === 'podcast') {
      await togglePodcastPlayPause();
      return;
    }
    loadingEpisodeId = ep.id;
    try {
      claimPodcastSession();
      await releaseSpotifyForPodcast();
      const ctrl = new AbortController();
      const timeout = setTimeout(() => ctrl.abort(), 25_000);
      const r = await fetch('/api/podcasts/play', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          episode_uri: ep.uri,
          episode_title: ep.name,
          show_id: drilledShow?.show_id ?? '',
        }),
        signal: ctrl.signal,
      }).finally(() => clearTimeout(timeout));
      const data = await r.json();
      if (data.ok) {
        if (data.player) adoptPodcastPlayer(data.player as Partial<PodcastPlayerState>, true, true);
        else {
          activeEpisodeId = ep.id;
          activePodcastId = drilledShow?.show_id ?? '';
        }
        openPodcastQueue();
      } else {
        const detail = String(data.detail || data.error || '').trim();
        if (detail) showFeedback(detail, { kind: 'error' });
      }
    } catch (e) {
      showFeedback((e as Error).message || 'POST /api/podcasts/play fejlede', { kind: 'error' });
    } finally {
      loadingEpisodeId = '';
    }
  }

  function formatPodcastDate(iso: string): string {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return iso;
      return d.toLocaleDateString('da-DK', { day: 'numeric', month: 'short', year: 'numeric' });
    } catch {
      return iso;
    }
  }

  function formatEpisodeDuration(ms: number): string {
    if (!ms || ms <= 0) return '';
    const totalMin = Math.round(ms / 60000);
    if (totalMin < 60) return `${totalMin} min`;
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    return m === 0 ? `${h} t` : `${h} t ${m} min`;
  }

  function formatProgress(ms: number): string {
    const total = Math.max(0, Math.floor((ms || 0) / 1000));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  function isPodcastTransport(): boolean {
    return playlist.activeTransport === 'podcast';
  }

  function speakerNowPlaying() {
    const m5 = store.devices.find((d) => /m5/i.test(d.name || ''));
    if (m5 && store.nowPlaying[m5.id]?.name) return store.nowPlaying[m5.id];
    for (const np of Object.values(store.nowPlaying)) {
      if (np.name) return np;
    }
    return null;
  }

  function liveNowPlaying() {
    const speaker = speakerNowPlaying();
    return nowPlayingFromSession({
      transport: playlist.activeTransport,
      podcastTitle: activePodcastPlayer.episodeTitle || playlist.podcastEpisodeTitle,
      podcastArtist: activePodcastPlayer.showTitle || playlist.podcastShowTitle,
      musicTitle: playlist.spotifyTitle,
      musicArtist: playlist.spotifyArtist,
      speakerTitle: speaker?.name,
      speakerArtist: speaker?.artist,
    });
  }

  function liveIsPlaying() {
    if (playlist.activeTransport === 'podcast') return activePodcastPlayer.playing || playlist.podcastPlaying;
    if (playlist.activeTransport === 'spotify') return playlist.spotifyPlaying;
    const speaker = speakerNowPlaying();
    if (typeof speaker?.playing === 'boolean') return speaker.playing;
    return false;
  }

  async function toggleNpPlayback() {
    if (playlist.activeTransport === 'podcast') {
      await togglePodcastPlayPause();
      return;
    }
    if (playlist.activeTransport === 'spotify') {
      await togglePlayPause();
      return;
    }
    const speaker = speakerNowPlaying();
    if (speaker?.name && speaker.playing) {
      try {
        await fetch('/api/spotify/pause', { method: 'POST' });
      } catch {
        /* */
      }
      return;
    }
    if (speaker?.name && speaker.playing === false) {
      try {
        await fetch('/api/spotify/resume', { method: 'POST' });
      } catch {
        /* */
      }
      return;
    }
    await togglePlayPause();
  }

  const liveNp = $derived.by(() => liveNowPlaying());
  const npPlaying = $derived.by(() => liveIsPlaying());

  // ── Cached radio playlists ───────────────────────────────────────────────
  let loadingPlaylistId = $state('');
  let loadingTrackIndex = $state(-1);
  let deletingTrackIndex = $state(-1);
  let activePlaylistId = $state('');
  let playlistInner = $state<HTMLDivElement>();
  let nextPlaylistCard = $state('');
  let prevPlaylistCard = $state('');

  type PlaylistTrack = { uri: string; name: string; artist: string; position?: number };
  let drilledPlaylist = $state<RadioPlaylist | null>(null);
  let drilledTracks = $state<PlaylistTrack[]>([]);

  function liveLibraryPlaylist(id: string, fallback: RadioPlaylist): RadioPlaylist {
    return radioLibrary.playlists.find((p) => p.id === id) ?? fallback;
  }

  function startCachedPlaylist(p: RadioPlaylist, index = 0) {
    const live = liveLibraryPlaylist(p.id, p);
    playlist.spotifyRadio = false;
    playlist.spotifyAlbumActive = false;
    playlist.savedPlaylistActive = true;
    playlist.savedPlaylistTitle = live.name;
    playlist.savedPlaylistQueue = live.tracks;
    playlist.savedPlaylistIndex = Math.max(0, Math.min(Math.max(0, live.tracks.length - 1), index));
    playlist.playListMode = 'playlist';
    paintNpFromQueues();
    return live;
  }

  function updatePlaylistScrollLabels() {
    if (!playlistInner) return;
    const rows = [...playlistInner.querySelectorAll<HTMLElement>('.playlist-card')];
    if (!rows.length) {
      nextPlaylistCard = '';
      prevPlaylistCard = '';
      return;
    }
    const currentTop = playlistInner.scrollTop;
    const next = rows.find((row) => row.offsetTop > currentTop + 12);
    const previous = [...rows].reverse().find((row) => row.offsetTop < currentTop - 12);
    nextPlaylistCard = next?.dataset.name ?? '';
    prevPlaylistCard = previous?.dataset.name ?? '';
  }

  function scrollPlaylistPage(direction: 1 | -1) {
    if (!playlistInner) return;
    const rows = [...playlistInner.querySelectorAll<HTMLElement>('.playlist-card')];
    if (!rows.length) return;
    const currentTop = playlistInner.scrollTop;
    const target = direction > 0
      ? rows.find((row) => row.offsetTop > currentTop + 12)
      : [...rows].reverse().find((row) => row.offsetTop < currentTop - 12);
    if (!target) {
      playlistInner.scrollTo({ top: direction > 0 ? playlistInner.scrollHeight : 0, behavior: 'smooth' });
      setTimeout(updatePlaylistScrollLabels, 350);
      return;
    }
    playlistInner.scrollTo({ top: target.offsetTop, behavior: 'smooth' });
    setTimeout(updatePlaylistScrollLabels, 350);
  }

  async function playSpotifyPlaylist(p: RadioPlaylist) {
    if (loadingPlaylistId) return;
    loadingPlaylistId = p.id;
    try {
      claimMusicSession();
      await releasePodcastForMusic();
      const live = startCachedPlaylist(p, 0);
      activePlaylistId = live.id;
      await playExactTrack(live.tracks[0]?.uri || '');
    } finally {
      loadingPlaylistId = '';
    }
  }

  function openPlaylistDrill(p: RadioPlaylist) {
    const live = liveLibraryPlaylist(p.id, p);
    drilledPlaylist = live;
    drilledTracks = live.tracks.map((track, i) => ({ ...track, position: i }));
  }

  function closePlaylistDrill() {
    drilledPlaylist = null;
    drilledTracks = [];
  }

  async function deletePlaylist(p: RadioPlaylist) {
    if (loadingPlaylistId) return;
    loadingPlaylistId = p.id;
    try {
      await deleteRadioPlaylist(p.id);
      if (activePlaylistId === p.id) activePlaylistId = '';
      if (drilledPlaylist?.id === p.id) closePlaylistDrill();
      showFeedback('Playliste slettet', { kind: 'success' });
    } catch (e) {
      showFeedback((e as Error).message || 'Kunne ikke slette', { kind: 'error' });
    } finally {
      loadingPlaylistId = '';
    }
  }

  async function deleteTrackFromPlaylist(track: PlaylistTrack, index: number) {
    if (!drilledPlaylist || deletingTrackIndex >= 0) return;
    deletingTrackIndex = index;
    try {
      await deleteRadioTrack(drilledPlaylist.id, index);

      drilledTracks = drilledTracks
        .filter((_, i) => i !== index)
        .map((row, i) => ({ ...row, position: i }));
      drilledPlaylist = { ...drilledPlaylist, tracks: drilledTracks };
      if (activePlaylistId === drilledPlaylist.id) {
        playlist.savedPlaylistQueue = playlist.savedPlaylistQueue.filter((_, i) => i !== index);
        if (playlist.savedPlaylistIndex >= playlist.savedPlaylistQueue.length) {
          playlist.savedPlaylistIndex = Math.max(0, playlist.savedPlaylistQueue.length - 1);
        } else if (playlist.savedPlaylistIndex > index) {
          playlist.savedPlaylistIndex -= 1;
        }
        paintNpFromQueues();
      }
      showFeedback('Sang fjernet', { kind: 'success' });
    } catch (e) {
      showFeedback((e as Error).message || 'Kunne ikke slette sang', { kind: 'error' });
    } finally {
      deletingTrackIndex = -1;
    }
  }

  async function playTrackFromDrilledPlaylist(track: PlaylistTrack, index: number) {
    if (!drilledPlaylist || loadingTrackIndex >= 0 || deletingTrackIndex >= 0) return;
    loadingTrackIndex = index;
    try {
      const live = liveLibraryPlaylist(drilledPlaylist.id, drilledPlaylist);
      const resolved = resolvePlaylistTap(track, live.tracks, playlist.savedPlaylistQueue, index);
      if (!resolved) {
        showFeedback('Sangens Spotify-sti mangler', { kind: 'error' });
        return;
      }
      claimMusicSession();
      await releasePodcastForMusic();
      startCachedPlaylist(live, resolved.index);
      activePlaylistId = live.id;
      await playExactTrack(resolved.uri);
    } finally {
      loadingTrackIndex = -1;
    }
  }

</script>

<svelte:head>
  <title>{isGarden() ? 'Haven · Ejdersted' : 'Ejdersted · HUE'}</title>
  <meta name="application-name" content={isGarden() ? 'Haven · Ejdersted' : 'Ejdersted · HUE'} />
  <meta name="apple-mobile-web-app-title" content={isGarden() ? 'Haven' : 'Ejdersted'} />
</svelte:head>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape') closePodcastSeek(); }} />

<main>
  <FeedbackOverlay />

  <!-- Splash screen for fullscreen entry -->
  {#if showSplash}
    <div class="splash" onclick={dismissSplash} role="button" tabindex="0" onkeydown={(e) => e.key === 'Enter' && dismissSplash()}>
      <span class="splash-title">{store.config.site === 'garden' ? 'HAVEN' : 'EJDERSTED'}</span>
    </div>
  {/if}

  <!-- Dim overlay -->
  <div
    class="dim-overlay"
    class:dimmed
    role="button"
    tabindex="-1"
    aria-label="Væk kiosk"
    onclick={() => noteActivity(true)}
    onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') noteActivity(true); }}
  ></div>

  <!-- Clock (above dim) -->
  {#if dimmed}
    <div class="clock">{clockTime}</div>
  {/if}

  <!-- Song streamer (above dim) -->
  {#if streamer}
    <div class="streamer">
      <span class="streamer-title">{streamer.title}</span>
      {#if streamer.artist}<span class="streamer-artist">{streamer.artist}</span>{/if}
    </div>
  {/if}

  <!-- ── Tab-nav (hidden in kiosk) ──────────────────────────────────────────── -->
  <nav>
    <button>LYD</button>
    <button>LYS</button>
    {#if !store.connected}
      <span class="conn">•</span>
    {/if}
  </nav>

  <!-- ── Advance arrow ─────────────────────────────────────────────────────── -->
  <button class="advance-arrow" onclick={advance} aria-label="Næste">
    <span class="arrow-label">{nextPageName}</span>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="9 6 15 12 9 18" />
    </svg>
  </button>

  <!-- ── Swipe container ───────────────────────────────────────────────────── -->
  <div class="pages" bind:this={pagesEl}>

    {#if enabled('camera') && store.config.site === 'garden'}
      <!-- PAGE · KAMERA (garden-first) ────────────────────────────────────── -->
      <section class="page page--primary-camera">
        <div class="col-header">KAMERA</div>
        <div class="scroll-inner camera-page">
          <CameraCard />
        </div>
      </section>
    {/if}

    <!-- PAGE · SOL (garden solar charge relay) ──────────────────────────── -->
    {#if enabled('solar')}
    <section class="page">
      <div class="col-header">SOL</div>
      <div class="scroll-inner">
        <Card
          name="Solcelle"
          status={store.solar.mode === 'on' ? 'Manuel · tændt' : store.solar.mode === 'off' ? 'Manuel · slukket' : 'Automatisk'}
          online={!!store.solar.relayOn}
        >
          <div class="solar">
            <div class="solar-state" class:on={store.solar.relayOn}>
              <span class="solar-state-dot"></span>
              <span class="solar-state-label">{store.solar.relayOn ? 'Solcelle tilsluttet' : 'Solcelle afbrudt'}</span>
            </div>

            <div class="solar-schedule">
              <div class="solar-sched-row">
                <span class="solar-sched-label">Tænder</span>
                <span class="solar-sched-time">{store.solar.onTime ?? '–'}</span>
              </div>
              <div class="solar-sched-row">
                <span class="solar-sched-label">Slukker</span>
                <span class="solar-sched-time">{store.solar.offTime ?? '–'}</span>
              </div>
              <div class="solar-sun">
                sol op {store.solar.sunrise ?? '–'} · sol ned {store.solar.sunset ?? '–'}
              </div>
            </div>

            <div class="solar-modes" role="group" aria-label="Solcelle-styring">
              <button type="button" class="action-btn" class:active={store.solar.mode === 'on'} onclick={() => store.setSolarMode('on')}>tænd</button>
              <button type="button" class="action-btn" class:active={store.solar.mode === 'auto'} onclick={() => store.setSolarMode('auto')}>auto</button>
              <button type="button" class="action-btn" class:active={store.solar.mode === 'off'} onclick={() => store.setSolarMode('off')}>sluk</button>
            </div>
          </div>
        </Card>
      </div>
    </section>
    {/if}

    <!-- PAGE 0 · LYD ─────────────────────────────────────────────────────── -->
    {#if enabled('audio') || enabled('spotify')}
    <section class="page">
      <div class="col-header">LYD</div>
      <div class="scroll-inner" bind:this={lydInner}>

        <!-- Now Playing (default card, always visible) -->
        {#if enabled('spotify')}
        <div class="np-card" data-name="Afspiller">
          <div class="np-info">
            {#if isPodcastTransport()}
              <span class="np-card-title">{liveNp.title}</span>
              <span class="np-card-artist">{liveNp.artist}</span>
              {#if activePodcastPlayer.queue.length > 1}
              <div class="np-track-nav" role="group" aria-label="Podcast">
                <button type="button" class="np-track-nav-btn" onclick={podcastPrevious} aria-label="Forrige episode">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <polyline points="15 18 9 12 15 6" />
                  </svg>
                </button>
                <button type="button" class="np-track-nav-btn" onclick={podcastNext} aria-label="Næste episode">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <polyline points="9 18 15 12 9 6" />
                  </svg>
                </button>
              </div>
              {/if}
              <button type="button" class="np-podcast-progress" onclick={openPodcastSeek} aria-label="Spol i podcast">
                {formatProgress(activePodcastPlayer.positionMs || playlist.podcastPositionMs)}
                {#if activePodcastPlayer.durationMs || playlist.podcastDurationMs}
                  / {formatProgress(activePodcastPlayer.durationMs || playlist.podcastDurationMs)}
                {/if}
              </button>
            {:else if liveNp.title}
              <span class="np-card-title">{liveNp.title}</span>
              {#if liveNp.artist}<span class="np-card-artist">{liveNp.artist}</span>{/if}
              <div class="np-track-nav" class:np-track-nav--single={activeQueue().length <= 1} role="group" aria-label="Sang">
                {#if activeQueue().length > 1}
                  <button type="button" class="np-track-nav-btn" onclick={spotifyPreviousTrack} aria-label="Forrige i køen">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                      <polyline points="15 18 9 12 15 6" />
                    </svg>
                  </button>
                {/if}
                <button
                  type="button"
                  class="np-save-btn"
                  class:saved={spotifySaved || radioSaveDone}
                  class:loading={saveLoading}
                  onclick={saveCurrentSelection}
                  disabled={saveLoading || !playlist.spotifyTrackUri || (isRadioPlaylistSaveable() && radioSaveDone)}
                  aria-label={isRadioPlaylistSaveable() ? 'Gem radio som playliste' : 'Gem sang'}
                >
                  {saveLoading ? '· · ·' : currentSaveLabel()}
                </button>
                {#if activeQueue().length > 1}
                  <button type="button" class="np-track-nav-btn" onclick={spotifyNextTrack} aria-label="Næste i køen">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                      <polyline points="9 18 15 12 9 6" />
                    </svg>
                  </button>
                {/if}
              </div>
              {#if playlist.spotifyNextTitle}
                <div class="np-next-streamer">
                  <span class="np-next-title">{playlist.spotifyNextTitle}</span>
                  {#if playlist.spotifyNextArtist}<span class="np-next-artist">{playlist.spotifyNextArtist}</span>{/if}
                </div>
              {/if}
            {:else}
              <span class="np-card-title np-card-title--muted">Ingen valgt sang</span>
              <span class="np-card-artist">Brug mikrofonen nedenfor for at tilføje til køen</span>
            {/if}
          </div>
          <div class="unified-vol unified-vol--horizontal np-volume" aria-label="Afspiller-volumen">
            <span class="unified-vol-label">vol</span>
            <input
              type="range"
              min="0"
              max="100"
              step="1"
              class="unified-vol-slider"
              value={unifiedVolume}
              oninput={(e) => { unifiedDragging = true; setUnifiedVolume(+(e.currentTarget as HTMLInputElement).value); }}
              onchange={(e) => { unifiedDragging = false; setUnifiedVolume(+(e.currentTarget as HTMLInputElement).value); }}
              onpointerup={() => { unifiedDragging = false; }}
              onpointercancel={() => { unifiedDragging = false; }}
              aria-label="Afspiller-volumen"
            />
            <span class="unified-vol-value">{unifiedVolume}</span>
          </div>
          <div class="action-row np-actions">
            <button type="button" class="action-btn" onclick={toggleNpPlayback}>
              {npPlaying ? 'pause' : 'play'}
            </button>
            {#if isPodcastTransport()}
              <button type="button" class="action-btn" onclick={openPodcastSeek}>spol</button>
              <button type="button" class="action-btn" onclick={goToPodcastNow}>afsnit</button>
            {:else}
              <button type="button" class="action-btn" class:active={isPlaylistContextActive()} class:loading={playlist.spotifyRadioLoading} onclick={togglePlaylistContext} disabled={playlist.spotifyRadioLoading}>
                {playlist.spotifyRadioLoading ? '· · ·' : 'playliste'}
              </button>
              <button type="button" class="action-btn" class:active={playlist.spotifyAlbumActive} class:loading={playlist.spotifyAlbumLoading} onclick={playAlbum} disabled={playlist.spotifyAlbumLoading}>
                {playlist.spotifyAlbumLoading ? '· · ·' : 'album'}
              </button>
            {/if}
          </div>
        </div>
        {/if}

        {#if enabled('audio')}
          {#if !isGarden() && store.devices.length > 0}
            <Card
              name="Højttalere"
              status={`${store.devices.filter((d) => store.volumes[d.id]?.online).length}/${store.devices.length} online`}
              online={store.devices.some((d) => store.volumes[d.id]?.online)}
              pulse={store.devices.some((d) => pulsingDevices[d.id])}
            >
              <div class="speaker-mixer">
                {#each store.devices as device (device.id)}
                  {@const vol = store.volumes[device.id] ?? { level: 0, online: false }}
                  {@const muted = muteState[device.id]?.muted ?? false}
                  <div class="speaker-channel" class:offline={!vol.online} class:muted>
                    <div class="speaker-fader">
                      <input
                        type="range"
                        min="0"
                        max="100"
                        step="1"
                        value={muted ? 0 : vol.level}
                        disabled={!vol.online}
                        aria-label={`Volumen ${device.name}`}
                        oninput={(e) => {
                          const level = +(e.currentTarget as HTMLInputElement).value;
                          if (muted && level > 0) muteState[device.id] = { muted: false, prev: muteState[device.id]?.prev ?? vol.level };
                          store.setVolume(device.id, level);
                        }}
                      />
                    </div>
                    <button
                      type="button"
                      class="speaker-label"
                      onclick={() => toggleMute(device.id, vol.level)}
                      disabled={!vol.online}
                      aria-label={muted ? `Slå ${device.name} til` : `Mute ${device.name}`}
                    >
                      <span class="speaker-name">{device.name}</span>
                      <span class="speaker-level">{muted ? 'muted' : `${vol.level}`}</span>
                    </button>
                    {#if store.nowPlaying[device.id]?.name}
                      {@const np = store.nowPlaying[device.id]}
                      <div class="speaker-now-playing">
                        <span>{np.name}</span>
                      </div>
                    {/if}
                  </div>
                {/each}
              </div>
            </Card>
          {/if}

          {#if !isGarden() && store.devices.length === 0 && store.connected}
            <p class="empty">Ingen højttalere fundet.</p>
          {/if}

          {#if audioTargets.length > 0}
            <Card
              name="Audio output"
              status={`${audioTargets.filter((target) => target.online).length}/${audioTargets.length} online`}
              online={audioTargets.some((target) => target.online)}
            >
              <div class="audio-targets">
                {#each audioTargets as target (target.id)}
                  <div class="audio-target" class:offline={!target.online}>
                    <div class="audio-target-row">
                      <div class="audio-target-main">
                        <span class="audio-target-name">{target.name}</span>
                        <span class="audio-target-status">
                          {target.online ? 'forbundet' : target.connected ? 'tilsluttet uden lydprofil' : 'ikke forbundet'}
                        </span>
                      </div>
                      <button
                        type="button"
                        class="action-btn audio-target-connect"
                        class:loading={connectingAudioTarget === target.id}
                        disabled={!!connectingAudioTarget}
                        onclick={() => reconnectAudioTarget(target.id)}
                      >
                        {connectingAudioTarget === target.id ? 'forbinder' : 'forbind igen'}
                      </button>
                    </div>
                    <div class="unified-vol unified-vol--horizontal audio-target-vol">
                      <span class="unified-vol-label">vol</span>
                      <input
                        type="range"
                        min="0"
                        max="100"
                        step="1"
                        class="unified-vol-slider"
                        value={readTargetVolume(target)}
                        disabled={!target.online}
                        oninput={(e) => {
                          unifiedDragging = true;
                          const level = +(e.currentTarget as HTMLInputElement).value;
                          unifiedVolume = level;
                          queueTargetVolume(target.id, level);
                        }}
                        onchange={(e) => {
                          unifiedDragging = false;
                          const level = +(e.currentTarget as HTMLInputElement).value;
                          unifiedVolume = level;
                          queueTargetVolume(target.id, level);
                        }}
                        onpointerup={() => { unifiedDragging = false; }}
                        onpointercancel={() => { unifiedDragging = false; }}
                        aria-label={`Volumen ${target.name}`}
                      />
                      <span class="unified-vol-value">{readTargetVolume(target)}</span>
                    </div>
                  </div>
                {/each}
              </div>
            </Card>
          {/if}
        {/if}

        <!-- Spotify Voice -->
        {#if enabled('spotify')}
        <Card
          name="Musik"
          status={playlist.spotifyRadioLoading
            ? 'Opbygger playliste…'
            : playlist.spotifyAlbumLoading
              ? 'Henter album…'
              : playlist.playListMode === 'playlist'
                ? (playlist.savedPlaylistTitle || 'Playliste')
                : playlist.playListMode === 'radio'
                  ? 'Playliste (lokal kø)'
                  : playlist.playListMode === 'album'
                    ? 'Album (lokal kø)'
                    : 'Mikrofon-kø'}
        >
          <SpotifyVoice onvoice={handleVoicePayload} />
        </Card>
        {/if}

      </div>
      <button type="button" class="card-arrow card-arrow--lyd" onclick={() => lydInner && advanceCard(lydInner, 'lyd')} aria-label="Næste kort">
        <span class="arrow-label">{nextLydCard}</span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
    </section>
    {/if}

    <!-- PAGE 1 · LYS ─────────────────────────────────────────────────────── -->
    {#if enabled('hue') || enabled('lights')}
    <section class="page">
      <div class="col-header">LYS</div>
      <div class="scroll-inner" bind:this={lysInner}>
        {#if enabled('hue')}
        {#if !store.connected}
          <div class="pair-wrap">
            <p class="pair-label">Hub ikke forbundet</p>
            <p class="pair-hint">
              Lys-kortet får live-data via WebSocket fra backend.<br />
              Start backend: <code>cd backend && python3.13 main.py</code><br />
              Med Vite-dev: <code>cd frontend && npm run dev</code> → åbn <strong>localhost:5173</strong>.<br />
              Eller åbn interfacet direkte på <strong>https://localhost:8443</strong> (samme origin som hubben).
            </p>
          </div>

        {:else if store.hueStatus.paired && store.hueRooms.length > 0}
          <!-- Rum-knobs (filtrér rum uden pærer fra) -->
          {#each store.hueRooms.filter(r => r.lights !== 0) as room (room.id)}
            <Card name={room.name} status={room.any_on ? 'tændt' : 'slukket'} online={room.any_on}>
              <div class="knob-wrap">
                <VolumeKnob
                  value={hueMuteState[room.id]?.muted ? 0 : room.brightness}
                  muted={!room.any_on || (hueMuteState[room.id]?.muted ?? false)}
                  disabled={hueMuteState[room.id]?.muted ?? false}
                  onchange={(v) => store.setHueBrightness(room.id, v)}
                  onmute={() => toggleHueMute(room.id, room.brightness)}
                />
              </div>
            </Card>
          {/each}

        {:else if store.hueStatus.paired && store.hueRooms.length === 0}
          <p class="empty">Forbundet — henter rum…</p>

        {:else}
          <!-- Pairing flow -->
          <div class="pair-wrap">
            {#if store.hueStatus.ip}
              <p class="pair-label">Bridge fundet</p>
              <p class="pair-ip">{store.hueStatus.ip}</p>
              <p class="pair-hint">
                Tryk på knappen på din Hue bridge,<br />og tap par herunder.
              </p>
            {:else}
              <p class="pair-label">Søger efter bridge…</p>
              <p class="pair-hint">
                Ingen bridge fundet via mDNS.<br />
                Indtast IP manuelt:
              </p>
            {/if}

            <form onsubmit={handlePair}>
              {#if !store.hueStatus.ip}
                <input type="text" bind:value={hueManualIp}
                  placeholder="Bridge IP (f.eks. 192.168.1.10)"
                  inputmode="url" autocomplete="off" />
              {/if}
              <button type="submit" class="btn-primary" disabled={huePairing}>
                {huePairing ? '…' : 'par'}
              </button>
            </form>
          </div>
        {/if}
        {:else if enabled('lights')}
          {#if store.lights.length === 0}
            <Card name="gårdlys" status="ikke parret" online={false}>
              <div class="pair-wrap">
                <p class="pair-label">gårdlys</p>
                <p class="pair-hint">
                  Par lampen til have-WiFi i LEDVANCE SMART+ appen.<br />
                  Hubben finder den bagefter på LAN.
                </p>
              </div>
            </Card>
          {:else}
            {#each store.lights as light (light.id)}
              <Card name="gårdlys" status={lightStatus(light)} online={light.online && light.any_on}>
                <div class="garden-light">
                  <div class="unified-vol unified-vol--horizontal garden-light-row">
                    <span class="unified-vol-label">lys</span>
                    <input
                      type="range"
                      min="0"
                      max="100"
                      step="1"
                      class="unified-vol-slider"
                      value={light.brightness}
                      disabled={!light.online}
                      oninput={(e) => store.setLightBrightness(light.id, +(e.currentTarget as HTMLInputElement).value)}
                      aria-label="Lysstyrke"
                    />
                    <span class="unified-vol-value">{light.brightness}</span>
                  </div>
                  {#if light.has_color}
                    <div class="garden-light-row garden-light-color">
                      <span class="unified-vol-label">farve</span>
                      <input
                        class="hue-slider"
                        type="range"
                        min="0"
                        max="360"
                        step="1"
                        value={light.hue ?? 30}
                        disabled={!light.online}
                        oninput={(e) => store.setLightColor(light.id, +(e.currentTarget as HTMLInputElement).value, light.sat ?? 80)}
                        aria-label="Farve"
                      />
                      <span class="garden-light-swatch" style="background: {light.hex || hsvToHex(light.hue ?? 30, light.sat ?? 80)}"></span>
                    </div>
                    <div class="garden-light-presets">
                      <button
                        type="button"
                        class="action-btn"
                        class:active={light.mode !== 'colour'}
                        disabled={!light.online}
                        onclick={() => store.setLightWhite(light.id)}
                      >hvid</button>
                      {#each lightColorPresets as preset (preset.id)}
                        <button
                          type="button"
                          class="action-btn"
                          class:active={light.mode === 'colour' && (light.hue ?? 30) === preset.hue}
                          disabled={!light.online}
                          onclick={() => store.setLightColor(light.id, preset.hue, preset.sat)}
                        >{preset.label}</button>
                      {/each}
                    </div>
                  {/if}
                  {#if !light.online}
                    <button
                      type="button"
                      class="action-btn"
                      class:loading={connectingLight === light.id}
                      disabled={!!connectingLight}
                      onclick={() => reconnectLight(light.id)}
                    >{connectingLight === light.id ? 'forbinder' : 'forbind'}</button>
                  {/if}
                </div>
              </Card>
            {/each}
          {/if}
        {/if}

      </div>
      <button type="button" class="card-arrow card-arrow--lys" onclick={() => lysInner && advanceCard(lysInner, 'lys')} aria-label="Næste kort">
        <span class="arrow-label">{nextLysCard}</span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
    </section>
    {/if}

    <!-- PAGE 2 · PLAYLISTER ──────────────────────────────────────────────── -->
    {#if enabled('playlists')}
    <section class="page">
      {#if drilledPlaylist}
        <div class="col-header drill-header">
          <button type="button" class="drill-back" onclick={closePlaylistDrill} aria-label="Tilbage til playliste-liste">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="15 6 9 12 15 18" />
            </svg>
            <span class="drill-back-label">{drilledPlaylist.name}</span>
          </button>
        </div>
      {:else}
        <div class="col-header">PLAYLISTER</div>
      {/if}

      <div class="scroll-inner list-scroll" bind:this={playlistInner} onscroll={updatePlaylistScrollLabels}>
        {#if drilledPlaylist}
          {#if drilledTracks.length === 0}
            <p class="empty">Ingen sange fundet.</p>
          {:else}
            {#each drilledTracks as track, i (track.uri + i)}
              <div class="playlist-track-row">
                <button
                  type="button"
                  class="episode-row playlist-track-main"
                  class:active={activePlaylistId === drilledPlaylist.id && playlist.savedPlaylistIndex === i && playlist.spotifyPlaying}
                  class:loading={loadingTrackIndex === i}
                  onclick={() => playTrackFromDrilledPlaylist(track, i)}
                >
                  <span class="episode-meta-top">
                    {#if loadingTrackIndex === i}
                      · · ·
                    {:else}
                      {i + 1}
                    {/if}
                  </span>
                  <span class="episode-title">{track.name}</span>
                  <span class="podcast-meta">{track.artist}</span>
                </button>
                <button
                  type="button"
                  class="playlist-track-delete"
                  class:loading={deletingTrackIndex === i}
                  onclick={() => deleteTrackFromPlaylist(track, i)}
                  disabled={deletingTrackIndex >= 0 || loadingTrackIndex >= 0}
                  aria-label={`Slet ${track.name} fra playliste`}
                >
                  {#if deletingTrackIndex === i}
                    · · ·
                  {:else}
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                      <polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /><path d="M10 11v6" /><path d="M14 11v6" />
                    </svg>
                  {/if}
                </button>
              </div>
            {/each}
            <button type="button" class="playlist-delete-row" onclick={() => deletePlaylist(drilledPlaylist!)} aria-label="Slet playliste">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /><path d="M10 11v6" /><path d="M14 11v6" />
              </svg>
              <span>Slet playliste</span>
            </button>
          {/if}
        {:else if radioLibrary.loading && radioLibrary.playlists.length === 0}
          <p class="empty">Henter playlister…</p>
        {:else if radioLibrary.error && radioLibrary.playlists.length === 0}
          <p class="empty">{radioLibrary.error}</p>
        {:else if radioLibrary.playlists.length === 0}
          <p class="empty">Ingen radio-playlister gemt endnu.</p>
        {:else}
          {#each radioLibrary.playlists as p (p.id)}
            <div
              class="podcast-card playlist-card"
              class:active={activePlaylistId === p.id}
              class:loading={loadingPlaylistId === p.id}
              data-name={p.name}
            >
              <button
                type="button"
                class="podcast-card-main"
                onclick={() => playSpotifyPlaylist(p)}
                aria-label={`Spil playlisten ${p.name}`}
              >
                <div class="podcast-cover playlist-text-cover" aria-hidden="true">
                  <span class="playlist-cover-title">{p.seedName || p.name}</span>
                  {#if p.seedArtist}
                    <span class="playlist-cover-artist">{p.seedArtist}</span>
                  {/if}
                </div>
                <div class="podcast-info">
                  <span class="podcast-show">{p.name}</span>
                  <span class="podcast-meta">
                    {p.tracks.length} sange
                    {#if loadingPlaylistId === p.id}
                      · henter…
                    {:else if activePlaylistId === p.id}
                      · aktiv
                    {/if}
                  </span>
                </div>
              </button>
              <button
                type="button"
                class="podcast-drill"
                onclick={() => openPlaylistDrill(p)}
                aria-label={`Vis sange i ${p.name}`}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="9 6 15 12 9 18" />
                </svg>
              </button>
            </div>
          {/each}
        {/if}
      </div>

      {#if !drilledPlaylist}
        <button type="button" class="card-arrow list-arrow list-arrow--up" onclick={() => scrollPlaylistPage(-1)} aria-label="Forrige playliste">
          <span class="arrow-label">{prevPlaylistCard}</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="18 15 12 9 6 15" />
          </svg>
        </button>

        <button type="button" class="card-arrow list-arrow list-arrow--down" onclick={() => scrollPlaylistPage(1)} aria-label="Næste playliste">
          <span class="arrow-label">{nextPlaylistCard}</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      {/if}
    </section>
    {/if}

    <!-- PAGE 3 · PODCAST ──────────────────────────────────────────────────── -->
    {#if enabled('podcasts')}
    <section class="page" data-page="podcast">
      {#if showPodcastQueue}
        <div class="col-header drill-header">
          <button type="button" class="drill-back" onclick={closePodcastQueue} aria-label="Tilbage til podcast-liste">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="15 6 9 12 15 18" />
            </svg>
            <span class="drill-back-label">Afspiller nu</span>
          </button>
        </div>
      {:else if drilledShow}
        <div class="col-header drill-header">
          <button type="button" class="drill-back" onclick={closeDrill} aria-label="Tilbage til podcast-liste">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="15 6 9 12 15 18" />
            </svg>
            <span class="drill-back-label">{drilledShow.show_name}</span>
          </button>
        </div>
      {:else}
        <div class="col-header">PODCAST</div>
      {/if}

      <div class="scroll-inner list-scroll" bind:this={podcastInner} onscroll={updatePodcastScrollLabels}>
        {#if showPodcastQueue}
          <div class="podcast-queue-view" data-podcast-current="true">
            <div class="podcast-queue-now">
              <span class="podcast-show">Spiller nu</span>
              <span class="podcast-episode">{activePodcastPlayer.episodeTitle || playlist.podcastEpisodeTitle || 'Podcast'}</span>
              <span class="podcast-meta">
                {activePodcastPlayer.showTitle || playlist.podcastShowTitle || 'Podcast'}
                · {formatProgress(activePodcastPlayer.positionMs || playlist.podcastPositionMs)}
                {#if activePodcastPlayer.durationMs || playlist.podcastDurationMs}
                  / {formatProgress(activePodcastPlayer.durationMs || playlist.podcastDurationMs)}
                {/if}
              </span>
            </div>
            <div class="podcast-player-panel podcast-player-panel--queue">
              <div class="podcast-progress-row">
                <span>{formatProgress(activePodcastPlayer.positionMs || playlist.podcastPositionMs)}</span>
                <input
                  type="range"
                  min="0"
                  max={Math.max(1, activePodcastPlayer.durationMs || playlist.podcastDurationMs)}
                  step="1000"
                  value={activePodcastPlayer.positionMs || playlist.podcastPositionMs}
                  oninput={(e) => {
                    seekingPodcast = true;
                    activePodcastPlayer.positionMs = +(e.currentTarget as HTMLInputElement).value;
                  }}
                  onchange={(e) => seekPodcastTo(+(e.currentTarget as HTMLInputElement).value)}
                  aria-label="Spol i podcast-afsnit"
                />
                <span>{formatProgress(activePodcastPlayer.durationMs || playlist.podcastDurationMs)}</span>
              </div>
              <div class="podcast-controls">
                <button type="button" class="action-btn" onclick={() => seekPodcast(-30)}>-30s</button>
                <button type="button" class="action-btn" onclick={togglePodcastPlayPause}>
                  {activePodcastPlayer.playing || playlist.podcastPlaying ? 'pause' : 'play'}
                </button>
                <button type="button" class="action-btn" onclick={() => seekPodcast(30)}>+30s</button>
              </div>
              <div class="podcast-queue-actions podcast-queue-actions--detail">
                <button type="button" class="action-btn" onclick={() => void refreshPodcastPlayer()}>fortsæt</button>
                <button type="button" class="action-btn" onclick={clearPodcastQueue}>ryd kø</button>
              </div>
            </div>

          </div>
        {:else if drilledShow}
          {#if drilledLoading && drilledEpisodes.length === 0}
            <p class="empty">Henter afsnit…</p>
          {:else if drilledEpisodes.length === 0}
            <p class="empty">{drilledError || 'Ingen afsnit fundet.'}</p>
          {:else}
            {#each drilledEpisodes as ep (ep.id)}
              <button
                type="button"
                class="episode-row"
                class:active={activeEpisodeId === ep.id}
                class:loading={loadingEpisodeId === ep.id}
                onclick={() => playEpisode(ep)}
              >
                <span class="episode-meta-top">
                  {formatPodcastDate(ep.release_date)}
                  {#if ep.duration_ms}
                    <span class="episode-dot">·</span> {formatEpisodeDuration(ep.duration_ms)}
                  {/if}
                  {#if loadingEpisodeId === ep.id}
                    <span class="episode-dot">·</span> starter…
                  {:else if activeEpisodeId === ep.id}
                    <span class="episode-dot">·</span> afspiller
                  {/if}
                </span>
                <span class="episode-title">{ep.name}</span>
              </button>
            {/each}
            {#if drilledHasMore}
              <button
                type="button"
                class="episode-more"
                onclick={loadMoreEpisodes}
                disabled={drilledLoadingMore}
              >
                {drilledLoadingMore ? '· · ·' : 'Hent flere afsnit'}
              </button>
            {/if}
          {/if}
        {:else if podcastsLoading && podcasts.length === 0}
          <p class="empty">Henter podcasts…</p>
        {:else if podcastsError && podcasts.length === 0}
          <p class="empty">{podcastsError}</p>
        {:else if podcasts.length === 0}
          <p class="empty">Ingen podcasts.</p>
        {:else}
          {#if activePodcastPlayer.active}
            <div class="podcast-card podcast-now" data-name="Afspiller nu" data-podcast-current="true">
              <button type="button" class="podcast-card-main" onclick={openPodcastQueue}>
                <div class="podcast-cover podcast-cover--now"></div>
                <div class="podcast-info">
                  <span class="podcast-show">Afspiller nu</span>
                  <span class="podcast-episode">{activePodcastPlayer.episodeTitle}</span>
                  <span class="podcast-meta">
                    Spiller nu · {activePodcastPlayer.showTitle}
                    · {formatProgress(activePodcastPlayer.positionMs)}
                    {#if activePodcastPlayer.durationMs}/ {formatProgress(activePodcastPlayer.durationMs)}{/if}
                  </span>
                </div>
              </button>
              <button
                type="button"
                class="podcast-drill"
                onclick={openPodcastQueue}
                aria-label="Åbn afspiller nu"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="9 6 15 12 9 18" />
                </svg>
              </button>
            </div>
          {/if}
          {#each podcasts as p (p.show_id)}
            <div
              class="podcast-card"
              class:active={activePodcastId === p.show_id}
              class:loading={loadingPodcastId === p.show_id}
              data-name={p.show_name}
            >
              <button
                type="button"
                class="podcast-card-main"
                onclick={() => playPodcast(p.show_id)}
                aria-label={`Spil seneste afsnit af ${p.show_name}`}
              >
                {#if p.show_image}
                  <img class="podcast-cover" src={p.show_image} alt="" loading="lazy" />
                {:else}
                  <div class="podcast-cover podcast-cover--empty"></div>
                {/if}
                <div class="podcast-info">
                  <span class="podcast-show">{p.show_name}</span>
                  <span class="podcast-episode">{p.episode_name}</span>
                  <span class="podcast-meta">
                    {formatPodcastDate(p.episode_release_date)}
                    {#if loadingPodcastId === p.show_id}
                      · starter…
                    {:else if activePodcastId === p.show_id}
                      · afspiller — tap for pause
                    {/if}
                  </span>
                </div>
              </button>
              <button
                type="button"
                class="podcast-drill"
                onclick={() => openDrill(p)}
                aria-label={`Vis alle afsnit af ${p.show_name}`}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="9 6 15 12 9 18" />
                </svg>
              </button>
            </div>
          {/each}
        {/if}
      </div>

      {#if !drilledShow}
        <button type="button" class="card-arrow list-arrow list-arrow--up" onclick={() => scrollPodcastPage(-1)} aria-label="Forrige podcast">
          <span class="arrow-label">{prevPodcastCard}</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="18 15 12 9 6 15" />
          </svg>
        </button>

        <button type="button" class="card-arrow list-arrow list-arrow--down" onclick={() => scrollPodcastPage(1)} aria-label="Næste podcast">
          <span class="arrow-label">{nextPodcastCard}</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      {/if}
    </section>
    {/if}

    <!-- PAGE 4 · KAMERA ──────────────────────────────────────────────────── -->
    {#if enabled('camera') && store.config.site !== 'garden'}
    <section class="page">
      <div class="col-header">KAMERA</div>
      <div class="scroll-inner camera-page">
        <CameraCard />
      </div>
    </section>
    {/if}

  </div>

  {#if podcastSeekOpen && isPodcastTransport()}
    <div class="seek-backdrop" transition:fade={{ duration: 140 }}>
      <button type="button" class="seek-underlay" aria-label="Luk spoling" onclick={closePodcastSeek}></button>
      <div
        class="seek-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Spol i podcast"
      >
        <span class="seek-title">{activePodcastPlayer.episodeTitle || liveNp.title || 'Podcast'}</span>
        {#if activePodcastPlayer.showTitle || liveNp.artist}
          <span class="seek-show">{activePodcastPlayer.showTitle || liveNp.artist}</span>
        {/if}
        <div class="podcast-progress-row seek-slider">
          <span>{formatProgress(activePodcastPlayer.positionMs || playlist.podcastPositionMs)}</span>
          <input
            type="range"
            min="0"
            max={Math.max(1, activePodcastPlayer.durationMs || playlist.podcastDurationMs)}
            step="1000"
            value={activePodcastPlayer.positionMs || playlist.podcastPositionMs}
            oninput={(e) => {
              seekingPodcast = true;
              activePodcastPlayer.positionMs = +(e.currentTarget as HTMLInputElement).value;
            }}
            onchange={(e) => seekPodcastTo(+(e.currentTarget as HTMLInputElement).value)}
            aria-label="Spol i podcast-afsnit"
          />
          <span>{formatProgress(activePodcastPlayer.durationMs || playlist.podcastDurationMs)}</span>
        </div>
        <div class="seek-skips">
          <button type="button" class="action-btn" onclick={() => seekPodcast(-30)}>-30s</button>
          <button type="button" class="action-btn" onclick={() => seekPodcast(30)}>+30s</button>
        </div>
        <button type="button" class="action-btn seek-close" onclick={closePodcastSeek}>luk</button>
      </div>
    </div>
  {/if}

</main>

<style>
  /* ── Splash screen ────────────────────────────────────────────────────────── */
  .splash {
    position: fixed;
    inset: 0;
    z-index: 1000;
    background: #000;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }

  .splash-title {
    font-size: 1.1rem;
    font-weight: 300;
    letter-spacing: 0.35em;
    text-transform: uppercase;
    color: #9b9b9b;
    transition: color 0.3s;
  }

  .splash:active .splash-title {
    color: #f2f2f2;
  }

  /* ── Dim overlay (kiosk sleep) ────────────────────────────────────────────── */
  .dim-overlay {
    position: fixed;
    inset: 0;
    background: #000;
    opacity: 0;
    pointer-events: none;
    transition: opacity 1.5s ease;
    z-index: 999;
  }
  .dim-overlay.dimmed {
    opacity: 1;
    pointer-events: auto;
  }

  /* ── Clock: kun kontur (ingen fyld) — neutral, lavere luminans end hvid fyld ─ */
  .clock {
    position: fixed;
    inset: 0;
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: center;
    pointer-events: none;
    font-size: clamp(12rem, 38vw, 28rem);
    font-weight: 300;
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
    font-family: 'Roboto', -apple-system, system-ui, sans-serif;
    color: transparent;
    -webkit-text-fill-color: transparent;
    -webkit-text-stroke: 1.35px rgba(174, 174, 174, 1);
    text-shadow: none;
    animation: clock-in 1.5s ease both;
  }

  @keyframes clock-in {
    from { opacity: 0; transform: scale(0.96); }
    to   { opacity: 1; transform: scale(1); }
  }

  /* ── Song streamer ────────────────────────────────────────────────────────── */
  .streamer {
    position: fixed;
    z-index: 1001;
    bottom: 15%;
    left: 0;
    width: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    pointer-events: none;
  }

  .streamer-title {
    font-size: 1.1rem;
    font-weight: 300;
    letter-spacing: 0.06em;
    color: #f2f2f2;
    opacity: 0;
    animation: text-fade 5s ease 0.4s both;
  }

  .streamer-artist {
    font-size: 0.7rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #9b9b9b;
    opacity: 0;
    animation: text-fade 4.5s ease 0.8s both;
  }

  @keyframes text-fade {
    0%   { opacity: 0; transform: translateY(6px); }
    12%  { opacity: 1; transform: translateY(0); }
    80%  { opacity: 1; transform: translateY(0); }
    100% { opacity: 0; transform: translateY(-4px); }
  }

  main {
    height: 100dvh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* ── Nav (hidden — kiosk) ────────────────────────────────────────────────── */
  nav {
    display: none;
  }

  .conn {
    display: none;
  }

  /* ── Column headers ──────────────────────────────────────────────────────── */
  .col-header {
    display: flex;
    align-items: flex-end;
    flex-shrink: 0;
    height: 48px;
    padding: 0 32px 10px;
    font-size: 0.7rem;
    font-weight: 400;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: #9b9b9b;
    background: #000;
  }

  /* ── Pages (2-visible, horizontal carousel) ───────────────────────────── */
  .pages {
    flex: 1;
    display: flex;
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: none;
    gap: 0;
    touch-action: none;            /* prevent manual swipe — arrow only */
  }
  .pages::-webkit-scrollbar { display: none; }

  .page {
    flex: 0 0 50%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    position: relative;
  }
  .page--primary-camera {
    flex-basis: 50%;
  }

  /* ── Advance arrow ────────────────────────────────────────────────────────── */
  .advance-arrow {
    position: fixed;
    top: 0;
    right: 0;
    z-index: 10;
    height: 48px;
    display: flex;
    align-items: flex-end;
    gap: 4px;
    padding: 0 12px 10px 0;
    background: none;
    border: none;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    color: #9b9b9b;
    transition: color 0.2s;
  }
  .advance-arrow:active { color: #f2f2f2; }
  .advance-arrow svg {
    width: 18px;
    height: 18px;
    flex-shrink: 0;
    transform: translateY(2px);
  }

  .scroll-inner {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    touch-action: none;
    max-width: none;
    /* Luft til fixed card-arrow (label + gap + ikon + bund-margin) */
    padding: 0 0 calc(18px + 44px + env(safe-area-inset-bottom, 0px));
    gap: 0;
    scrollbar-width: none;
  }
  .scroll-inner::-webkit-scrollbar { display: none; }

  /* ── Card down-arrow ──────────────────────────────────────────────────────────
     Absolute indenfor sin .page så den følger horizontal carousel-rotation.
     Tidligere var den `position: fixed` til viewport, hvilket betød at LYS's pile
     overlappede PODCAST når den side stod i højre halvdel — og tap på podcast-pilen
     ramte LYS-scrolleren i stedet. */
  .card-arrow {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 6;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    gap: 2px;
    margin: 0;
    padding: 18px 0 max(18px, env(safe-area-inset-bottom, 0px));
    background: var(--black);
    border: none;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    color: #9b9b9b;
    transition: color 0.2s;
    pointer-events: auto;
  }
  /* --lyd og --lys-modifierne er bevaret som no-op for HTML-bagudkompatibilitet
     men har ingen effekt længere — pilen ligger altid 100% bredt indenfor sin page. */
  .card-arrow--lyd,
  .card-arrow--lys { }
  .card-arrow:active { color: #f2f2f2; }
  .card-arrow svg {
    width: 18px;
    height: 18px;
  }
  .list-arrow--up {
    top: 48px;
    bottom: auto;
    justify-content: flex-start;
    padding: 10px 0 10px;
  }
  .list-arrow--down {
    bottom: 0;
    padding: 10px 0 10px;
  }

  /* ── Shared arrow label ──────────────────────────────────────────────────────── */
  .arrow-label {
    font-size: 0.7rem;
    font-weight: 400;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    white-space: nowrap;
  }

  .knob-wrap {
    max-width: 200px;
    margin: 0 auto;
    align-self: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
  }

  .garden-light {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    align-self: stretch;
    height: 100%;
    width: 100%;
    gap: 22px;
    padding: 12px 8px;
  }

  .garden-light-row {
    width: min(420px, 100%);
  }

  .garden-light-color {
    display: flex;
    flex-direction: row;
    align-items: center;
    gap: 12px;
  }

  .garden-light-color .hue-slider {
    flex: 1;
    min-width: 0;
    height: 34px;
    border-radius: 17px;
  }

  .garden-light-color .hue-slider::-webkit-slider-thumb {
    width: 22px;
    height: 22px;
  }

  .garden-light-swatch {
    width: 22px;
    height: 22px;
    border-radius: 999px;
    flex-shrink: 0;
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.35);
  }

  .garden-light-presets {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 8px;
  }

  .speaker-mixer {
    display: flex;
    flex-direction: row;
    justify-content: center;
    align-items: stretch;
    gap: 18px;
    width: 100%;
    min-height: 220px;
    padding: 8px 6px 2px;
  }

  .speaker-channel {
    min-width: 74px;
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    color: #c2c2c2;
  }

  .speaker-channel.offline {
    opacity: 0.35;
  }

  .speaker-channel.muted {
    color: #555;
  }

  .speaker-fader {
    position: relative;
    width: 34px;
    flex: 1;
    min-height: 142px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .speaker-fader::before {
    content: '';
    position: absolute;
    top: 8px;
    bottom: 8px;
    left: 50%;
    width: 3px;
    transform: translateX(-50%);
    background: rgba(255, 255, 255, 0.28);
    border-radius: 999px;
    pointer-events: none;
  }

  .speaker-fader input {
    position: relative;
    z-index: 1;
    width: 148px;
    height: 34px;
    margin: 0;
    background: transparent;
    accent-color: #c2c2c2;
    transform: rotate(-90deg);
    transform-origin: center;
    cursor: pointer;
    -webkit-appearance: none;
    appearance: none;
  }

  .speaker-fader input::-webkit-slider-runnable-track {
    height: 1px;
    background: transparent;
  }

  .speaker-fader input::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 28px;
    height: 28px;
    margin-top: -12.5px;
    border-radius: 999px;
    border: 2px solid rgba(255, 255, 255, 0.72);
    background: #0080c8;
    box-shadow: 0 0 0 6px rgba(0, 128, 200, 0.18);
  }

  .speaker-fader input::-moz-range-track {
    height: 1px;
    background: transparent;
  }

  .speaker-fader input::-moz-range-thumb {
    width: 22px;
    height: 22px;
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.28);
    background: #111;
    box-shadow: 0 0 0 5px rgba(255, 255, 255, 0.035);
  }

  .speaker-label {
    max-width: 82px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    background: none;
    border: none;
    color: inherit;
    font: inherit;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }

  .speaker-label:disabled {
    cursor: default;
  }

  .speaker-name {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.68rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .speaker-level {
    font-size: 0.62rem;
    letter-spacing: 0.08em;
    color: #9b9b9b;
    font-variant-numeric: tabular-nums;
  }

  .speaker-now-playing {
    max-width: 82px;
    min-height: 24px;
    color: #555;
    font-size: 0.62rem;
    line-height: 1.25;
    text-align: center;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }

  .audio-targets {
    display: flex;
    flex-direction: column;
    gap: 10px;
    width: 100%;
    height: 100%;
    justify-content: center;
    padding: 0 4px;
  }

  .audio-target {
    display: flex;
    flex-direction: column;
    gap: 12px;
    width: 100%;
    padding: 6px 0 12px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.12);
  }

  .audio-target-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    width: 100%;
  }

  .audio-target.offline {
    opacity: 0.72;
  }

  .audio-target-main {
    display: flex;
    flex-direction: column;
    gap: 5px;
    min-width: 0;
  }

  .audio-target-name {
    color: #f2f2f2;
    font-size: 0.95rem;
    font-weight: 300;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .audio-target-status {
    color: #9b9b9b;
    font-size: 0.68rem;
    letter-spacing: 0.16em;
    text-transform: uppercase;
  }

  .audio-target-connect {
    flex: 0 0 auto;
    min-width: 116px;
    padding: 8px 0 8px 10px;
    text-align: right;
    font-size: 0.62rem;
  }

  .audio-target-vol {
    width: 100%;
  }

  .camera-page {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px 32px;
  }

  /* ── Solcelle-kort ───────────────────────────────────────────────────────── */
  .solar {
    display: flex;
    flex-direction: column;
    align-items: center;
    align-self: stretch;
    justify-content: space-evenly;
    height: 100%;
    width: 100%;
    gap: 14px;
    padding: 14px 6px;
  }

  .solar-state {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .solar-state-dot {
    width: 13px;
    height: 13px;
    border-radius: 50%;
    background: #333;
    transition: background 0.5s ease, box-shadow 0.5s ease;
  }

  .solar-state.on .solar-state-dot {
    background: var(--accent);
    box-shadow: 0 0 20px 3px rgba(0, 128, 200, 0.55);
  }

  .solar-state-label {
    color: #f2f2f2;
    font-size: 0.95rem;
    font-weight: 300;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }

  .solar-schedule {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
  }

  .solar-sched-row {
    display: flex;
    align-items: baseline;
    gap: 18px;
  }

  .solar-sched-label {
    color: #9b9b9b;
    font-size: 0.68rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    min-width: 84px;
    text-align: right;
  }

  .solar-sched-time {
    color: #f2f2f2;
    font-size: 1.7rem;
    font-weight: 200;
    letter-spacing: 0.04em;
    font-variant-numeric: tabular-nums;
  }

  .solar-sun {
    margin-top: 4px;
    color: #9b9b9b;
    font-size: 0.66rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }

  .solar-modes {
    display: flex;
    gap: 6px;
  }

  /* ── Now Playing card ────────────────────────────────────────────────────── */
  .np-card {
    height: calc(100dvh - 48px);
    min-height: calc(100dvh - 48px);
    max-height: calc(100dvh - 48px);
    overflow: hidden;
    display: grid;
    grid-template-rows: minmax(0, 1fr) 42px 34px;
    align-items: center;
    justify-items: center;
    gap: 6px;
    padding: 8px 24px 62px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.12);
    position: relative;
  }

  .np-track-nav {
    display: flex;
    flex-direction: row;
    align-items: center;
    justify-content: center;
    gap: 6px;
    margin-top: 2px;
  }

  .np-track-nav--single {
    margin-top: 4px;
  }

  .np-track-nav-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    background: none;
    border: none;
    padding: 10px 10px;
    margin: 0;
    color: #8a8a8a;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: color 0.15s;
  }

  .np-track-nav-btn svg {
    width: 20px;
    height: 20px;
    display: block;
  }

  .np-track-nav-btn:active {
    color: #0080c8;
  }

  .unified-vol--horizontal {
    width: min(300px, 100%);
    display: flex;
    flex-direction: row;
    align-items: center;
    justify-content: center;
    gap: 12px;
  }

  .np-volume {
    margin: 0;
  }

  .np-actions {
    width: min(340px, 100%);
    gap: 12px;
    align-self: center;
  }

  .np-podcast-progress {
    margin-top: 3px;
    color: #9b9b9b;
    font-size: 0.68rem;
    letter-spacing: 0.08em;
    font-variant-numeric: tabular-nums;
    background: none;
    border: none;
    padding: 4px 8px;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .np-podcast-progress:active {
    color: #c2c2c2;
  }

  .np-actions .action-btn {
    padding: 7px 8px;
    font-size: 0.62rem;
    letter-spacing: 0.16em;
  }

  .unified-vol-label {
    min-width: 30px;
    color: #c2c2c2;
    font-size: 0.64rem;
    letter-spacing: 0.16em;
    text-transform: uppercase;
  }

  .unified-vol-slider {
    position: relative;
    width: 100%;
    height: 34px;
    margin: 0;
    background: transparent;
    accent-color: #0080c8;
    cursor: pointer;
    -webkit-appearance: none;
    appearance: none;
  }

  .unified-vol-slider::-webkit-slider-runnable-track {
    height: 4px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.3);
  }

  .unified-vol-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 20px;
    height: 20px;
    margin-top: -8px;
    border-radius: 999px;
    border: 2px solid rgba(255, 255, 255, 0.72);
    background: #0080c8;
    box-shadow: 0 0 0 4px rgba(0, 128, 200, 0.18);
  }

  .unified-vol-slider::-moz-range-track {
    height: 4px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.3);
  }

  .unified-vol-slider::-moz-range-thumb {
    width: 20px;
    height: 20px;
    border-radius: 999px;
    border: 2px solid rgba(255, 255, 255, 0.72);
    background: #0080c8;
    box-shadow: 0 0 0 4px rgba(0, 128, 200, 0.18);
  }

  .unified-vol-value {
    min-width: 34px;
    text-align: center;
    font-size: 0.72rem;
    letter-spacing: 0.08em;
    color: #f2f2f2;
    font-variant-numeric: tabular-nums;
  }

  .np-info {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    max-width: 100%;
    min-height: 0;
    overflow: hidden;
  }

  .np-card-title {
    font-size: 1.04rem;
    font-weight: 300;
    color: #f2f2f2;
    text-align: center;
    letter-spacing: 0.02em;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }

  .np-card-title--muted {
    color: #9b9b9b;
  }

  .np-card-artist {
    font-size: 0.62rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #9b9b9b;
    text-align: center;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }

  .np-save-btn {
    background: none;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 999px;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    padding: 6px 10px;
    min-width: 66px;
    max-width: 128px;
    color: #c2c2c2;
    font-size: 0.58rem;
    font-weight: 300;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    transition: color 0.2s, border-color 0.2s;
  }
  .np-save-btn:active {
    color: #f2f2f2;
    border-color: rgba(255, 255, 255, 0.18);
  }
  .np-save-btn.saved {
    color: #c8e8ff;
    border-color: rgba(200, 232, 255, 0.18);
  }
  .np-save-btn:disabled {
    cursor: default;
    opacity: 0.55;
  }

  .np-card-next {
    font-size: 0.7rem;
    font-weight: 300;
    color: #c2c2c2;
    text-align: center;
    margin-top: 4px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
    opacity: 0.6;
  }

  .np-next-streamer {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 1px;
    margin-top: 3px;
    opacity: 0;
    animation: streamer-in 0.8s ease 0.2s forwards;
  }

  .np-status {
    margin-top: -4px;
    min-height: 14px;
    color: #9b9b9b;
    font-size: 0.65rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    text-align: center;
  }
  .np-next-title {
    font-size: 0.7rem;
    font-weight: 300;
    color: #c2c2c2;
    text-align: center;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }

  .np-next-artist {
    font-size: 0.56rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #8a8a8a;
    text-align: center;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }

  @keyframes streamer-in {
    from { opacity: 0; transform: translateY(6px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  /* ── Buttons ──────────────────────────────────────────────────────────────── */
  .btn-text {
    display: block;
    margin: 10px auto 0;
    background: none;
    border: none;
    color: #9b9b9b;
    font-size: 0.65rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    cursor: pointer;
    padding: 6px 12px;
    transition: color 0.2s;
  }
  .btn-text:hover { color: #c2c2c2; }

  .btn-outline {
    display: block;
    width: 100%;
    background: none;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    color: #9b9b9b;
    font-size: 0.8rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 16px;
    cursor: pointer;
    transition: border-color 0.2s, color 0.2s;
  }
  .btn-outline:hover { border-color: rgba(255,255,255,0.15); color: #c2c2c2; }

  .btn-primary {
    flex: 1;
    padding: 14px;
    border-radius: 12px;
    border: none;
    font-size: 0.8rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    cursor: pointer;
    background: #0080c8;
    color: #000;
    font-weight: 600;
    transition: opacity 0.15s;
  }
  .btn-primary:disabled { opacity: 0.4; cursor: default; }

  .btn-ghost {
    flex: 1;
    padding: 14px;
    border-radius: 12px;
    border: none;
    font-size: 0.8rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    cursor: pointer;
    background: rgba(255, 255, 255, 0.05);
    color: #c2c2c2;
  }

  /* ── Forms ────────────────────────────────────────────────────────────────── */
  .add-wrap, form {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .form-row {
    display: flex;
    gap: 10px;
  }

  form input {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 12px;
    padding: 14px 16px;
    color: #f2f2f2;
    font-size: 0.9rem;
    outline: none;
    transition: border-color 0.2s;
    -webkit-appearance: none;
  }
  form input:focus { border-color: #0080c8; }

  /* ── Hue pairing ──────────────────────────────────────────────────────────── */
  .pair-wrap {
    display: flex;
    flex-direction: column;
    gap: 14px;
    min-height: calc(100dvh - 48px);
    justify-content: center;
    padding: 24px 32px;
  }

  .pair-label {
    font-size: 0.7rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: #9b9b9b;
  }

  .pair-ip {
    font-size: 1.4rem;
    font-weight: 200;
    color: #f2f2f2;
    letter-spacing: 0.04em;
  }

  .pair-hint {
    font-size: 0.85rem;
    color: #9b9b9b;
    line-height: 1.6;
  }

  .pair-hint code {
    font-size: 0.78rem;
    color: #7a7a7a;
  }

  .empty {
    text-align: center;
    color: #9b9b9b;
    font-size: 0.85rem;
    line-height: 1.6;
    padding: 40px 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: calc(100dvh - 48px - 80px);
  }

  /* ── Podcast cards ────────────────────────────────────────────────────────── */
  .podcast-card {
    display: flex;
    flex-direction: row;
    align-items: stretch;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    transition: background 0.18s;
  }
  .podcast-card.active {
    background: rgba(0, 128, 200, 0.06);
  }
  .podcast-now {
    background: rgba(0, 128, 200, 0.08);
  }
  .podcast-card.loading {
    opacity: 0.65;
  }
  .playlist-card {
    width: 100%;
    background: none;
    border-left: none;
    border-right: none;
    border-top: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .list-scroll {
    scroll-behavior: smooth;
    padding-top: 36px;
    padding-bottom: 56px;
    touch-action: pan-y;
  }

  .podcast-card-main {
    display: flex;
    flex-direction: row;
    align-items: center;
    gap: 16px;
    padding: 14px 8px 14px 24px;
    background: none;
    border: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    flex: 1;
    min-width: 0;
  }
  .podcast-card-main:active {
    background: rgba(255, 255, 255, 0.03);
  }

  .podcast-drill {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 56px;
    flex-shrink: 0;
    padding: 0 16px 0 8px;
    background: none;
    border: none;
    color: #8a8a8a;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: color 0.15s, background 0.18s;
  }
  .podcast-drill svg {
    width: 18px;
    height: 18px;
    display: block;
  }
  .podcast-drill:active {
    color: #f2f2f2;
    background: rgba(255, 255, 255, 0.03);
  }

  .playlist-delete-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 18px 24px;
    margin-top: 12px;
    background: none;
    border: none;
    border-top: 1px solid rgba(255, 255, 255, 0.06);
    color: #9b9b9b;
    font-size: 0.75rem;
    letter-spacing: 0.05em;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .playlist-delete-row svg {
    width: 16px;
    height: 16px;
    flex-shrink: 0;
  }
  .playlist-delete-row:active {
    color: #e44;
  }

  .podcast-cover {
    flex: 0 0 88px;
    width: 88px;
    height: 88px;
    border-radius: 8px;
    object-fit: cover;
    background: rgba(255, 255, 255, 0.04);
  }
  .podcast-cover--empty {
    background: linear-gradient(135deg, #1a1a1a, #2a2a2a);
  }
  .podcast-cover--now {
    background:
      radial-gradient(circle at 50% 50%, rgba(0, 128, 200, 0.34), transparent 55%),
      linear-gradient(135deg, #1a1a1a, #2a2a2a);
  }
  .playlist-text-cover {
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    gap: 4px;
    padding: 9px;
    background:
      radial-gradient(circle at 22% 18%, rgba(255, 255, 255, 0.1), transparent 34%),
      linear-gradient(145deg, #242424, #111);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);
  }
  .playlist-cover-title,
  .playlist-cover-artist {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    overflow: hidden;
    line-height: 1.05;
    text-transform: uppercase;
    word-break: break-word;
  }
  .playlist-cover-title {
    color: #f1f1f1;
    font-size: 0.64rem;
    font-weight: 500;
    letter-spacing: 0.08em;
    -webkit-line-clamp: 3;
  }
  .playlist-cover-artist {
    color: #8a8a8a;
    font-size: 0.5rem;
    letter-spacing: 0.12em;
    -webkit-line-clamp: 2;
  }

  .podcast-info {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
    flex: 1;
  }

  .podcast-show {
    font-size: 0.65rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: #6f6f6f;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .podcast-episode {
    font-size: 0.95rem;
    font-weight: 300;
    color: #f2f2f2;
    line-height: 1.3;
    /* op til 3 linjer for længere afsnit-titler */
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .podcast-card.active .podcast-episode {
    color: #c8e8ff;
  }

  .podcast-meta {
    font-size: 0.7rem;
    letter-spacing: 0.06em;
    color: #9b9b9b;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .podcast-queue-actions {
    display: flex;
    justify-content: center;
    gap: 10px;
    padding: 10px 20px 14px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  }

  .podcast-queue-actions--detail {
    padding: 2px 0 0;
    border-bottom: none;
  }

  .podcast-queue-view {
    display: flex;
    flex-direction: column;
    min-height: calc(100dvh - 48px - 56px);
  }

  .podcast-queue-now {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 22px 24px 14px;
    background: rgba(0, 128, 200, 0.08);
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  }

  .podcast-queue-now .podcast-episode {
    font-size: 1.02rem;
    -webkit-line-clamp: 2;
  }

  .podcast-player-panel--queue {
    padding-top: 14px;
  }

  .empty--compact {
    min-height: 80px;
    padding: 18px 24px;
  }

  .podcast-player-panel {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 10px 24px 16px;
    background: rgba(0, 128, 200, 0.055);
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  }

  .podcast-progress-row {
    display: grid;
    grid-template-columns: 42px minmax(0, 1fr) 48px;
    align-items: center;
    gap: 10px;
    color: #c2c2c2;
    font-size: 0.64rem;
    font-variant-numeric: tabular-nums;
  }

  .podcast-progress-row input {
    width: 100%;
    accent-color: #0080c8;
  }

  .podcast-controls {
    display: flex;
    justify-content: center;
    gap: 10px;
  }

  .podcast-controls .action-btn,
  .podcast-queue-actions .action-btn {
    padding: 7px 10px;
    font-size: 0.6rem;
  }

  .seek-backdrop {
    position: fixed;
    inset: 0;
    z-index: 40;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }

  .seek-underlay {
    position: absolute;
    inset: 0;
    border: none;
    background: rgba(0, 0, 0, 0.48);
    cursor: pointer;
  }

  .seek-modal {
    position: relative;
    width: min(340px, 42vw);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    padding: 22px 20px 14px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 24px;
    background: rgba(18, 18, 18, 0.88);
    box-shadow: 0 22px 70px rgba(0, 0, 0, 0.45);
    -webkit-backdrop-filter: blur(28px) saturate(1.4);
    backdrop-filter: blur(28px) saturate(1.4);
  }

  .seek-title {
    max-width: 100%;
    color: #f2f2f2;
    font-size: 0.92rem;
    font-weight: 300;
    letter-spacing: 0.02em;
    text-align: center;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .seek-show {
    margin-top: -6px;
    max-width: 100%;
    color: #9b9b9b;
    font-size: 0.58rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    text-align: center;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .seek-slider {
    width: 100%;
    margin-top: 4px;
  }

  .seek-slider input {
    height: 34px;
    accent-color: #0080c8;
  }

  .seek-skips {
    display: flex;
    justify-content: center;
    gap: 8px;
  }

  .seek-skips .action-btn,
  .seek-close {
    padding: 8px 12px;
    font-size: 0.62rem;
  }

  .hue-slider {
    -webkit-appearance: none;
    appearance: none;
    display: block;
    box-sizing: border-box;
    width: 100%;
    min-width: 0;
    height: 34px;
    border-radius: 17px;
    outline: none;
    background: linear-gradient(to right, #ff0000, #ffff00, #00ff00, #00ffff, #0000ff, #ff00ff, #ff0000);
  }

  .hue-slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background: #f2f2f2;
    border: 2px solid #111;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.4);
  }

  /* ── Drill-in (per show) ──────────────────────────────────────────────── */
  .drill-header {
    padding: 0 0 10px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .drill-back {
    display: flex;
    align-items: flex-end;
    gap: 8px;
    width: 100%;
    height: 100%;
    padding: 0 24px 0 16px;
    background: none;
    border: none;
    color: #f2f2f2;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    text-align: left;
  }
  .drill-back:active {
    color: #0080c8;
  }
  .drill-back svg {
    width: 18px;
    height: 18px;
    flex-shrink: 0;
    transform: translateY(-2px);
    color: #c2c2c2;
  }
  .drill-back-label {
    font-size: 0.7rem;
    font-weight: 400;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: #f2f2f2;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .episode-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 14px 24px;
    margin: 0;
    background: none;
    border: none;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: background 0.18s;
    width: 100%;
  }
  .episode-row:active {
    background: rgba(255, 255, 255, 0.03);
  }
  .episode-row.active {
    background: rgba(0, 128, 200, 0.06);
  }
  .episode-row.loading {
    opacity: 0.65;
  }

  .playlist-track-row {
    display: flex;
    align-items: stretch;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  }
  .playlist-track-main {
    flex: 1;
    border-bottom: none;
  }
  .playlist-track-delete {
    width: 58px;
    padding: 0 22px 0 8px;
    background: none;
    border: none;
    color: #555;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .playlist-track-delete svg {
    width: 16px;
    height: 16px;
  }
  .playlist-track-delete:active {
    color: #e44;
  }
  .playlist-track-delete:disabled {
    opacity: 0.45;
  }

  .episode-meta-top {
    font-size: 0.65rem;
    letter-spacing: 0.08em;
    color: #9b9b9b;
    text-transform: uppercase;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .episode-dot {
    color: #6a6a6a;
    margin: 0 2px;
  }

  .episode-title {
    font-size: 0.9rem;
    font-weight: 300;
    color: #f2f2f2;
    line-height: 1.35;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .episode-row.active .episode-title {
    color: #c8e8ff;
  }

  .episode-more {
    display: block;
    margin: 16px auto;
    padding: 12px 24px;
    background: none;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    color: #c2c2c2;
    font-size: 0.75rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: color 0.18s, border-color 0.18s;
  }
  .episode-more:active {
    color: #f2f2f2;
    border-color: rgba(255, 255, 255, 0.2);
  }
  .episode-more:disabled {
    opacity: 0.5;
    cursor: default;
  }
</style>
