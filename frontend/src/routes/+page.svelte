<script lang="ts">
  import { onMount } from 'svelte';
  import { fade } from 'svelte/transition';
  import { store, type AudioTargetStatus } from '$lib/ws.svelte';
  import Card from '$lib/Card.svelte';
  import VolumeKnob from '$lib/VolumeKnob.svelte';
  import SpotifyVoice from '$lib/SpotifyVoice.svelte';
  import FeedbackOverlay from '$lib/FeedbackOverlay.svelte';
  import CameraCard from '$lib/CameraCard.svelte';
  import FossibotCard from '$lib/FossibotCard.svelte';
  import SwitchbotCard from '$lib/SwitchbotCard.svelte';
  import LightsCard from '$lib/LightsCard.svelte';
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
    playBrowsedTrack,
    setPodcastTransportFromPlayer,
    clearPodcastTransport,
  } from '$lib/playlistHub.svelte';
  import { init as initSpotifyWebPlayer } from '$lib/spotifyPlayer.svelte';

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

  function isPortraitViewport() {
    return window.innerHeight >= window.innerWidth;
  }

  function lockLandscape() {
    // Wall kiosk is already landscape (ADB). Do not force a phone into
    // landscape — that keeps the 2-column layout after the splash tap.
    if (isPortraitViewport()) return;
    const orientation = screen.orientation as ScreenOrientation & {
      lock?: (orientation: 'landscape') => Promise<void>;
    };
    orientation?.lock?.('landscape').catch(() => {});
  }

  function enterFullscreenFromGesture() {
    const root = document.documentElement as HTMLElement & {
      webkitRequestFullscreen?: () => void;
    };
    try {
      const pending = root.requestFullscreen?.({ navigationUI: 'hide' });
      if (pending && typeof pending.catch === 'function') {
        pending.catch(() => {
          try { root.webkitRequestFullscreen?.(); } catch { /* already fullscreen or denied */ }
        });
      } else {
        root.webkitRequestFullscreen?.();
      }
    } catch {
      try { root.webkitRequestFullscreen?.(); } catch { /* ignore */ }
    }
  }

  function requestFullscreenAndKiosk() {
    enterFullscreenFromGesture();
    setTimeout(lockLandscape, 250);
    requestWakeLock();
    if (enabled('adbKiosk')) fetch('/api/kiosk', { method: 'POST' }).catch(() => {});
  }

  function resetDim(wakeKiosk = false) {
    if (showSplash) return;
    // pointermove is not a fullscreen user-activation. If it clears dimmed,
    // the real tap finds the clock already gone and never retries.
    if (dimmed && !wakeKiosk) return;
    lastActivityAt = Date.now();
    if (!dimmed) return;
    enterFullscreenFromGesture();
    if (enabled('adbKiosk')) fetch('/api/kiosk/wake', { method: 'POST' }).catch(() => {});
    setBrightness(255);
    requestWakeLock();
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

  let wakingSpeaker = $state('');

  async function wakeHouseSpeaker(deviceId: string, name: string, level: number) {
    if (wakingSpeaker) return;
    wakingSpeaker = deviceId;
    store.setVolume(deviceId, level);
    await new Promise((r) => setTimeout(r, 2200));
    const online = !!store.volumes[deviceId]?.online;
    if (!online) {
      showFeedback(`kunne ikke forbinde ${name}`, { kind: 'error', duration: 6000 });
    }
    wakingSpeaker = '';
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
    syncPageLayout();
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
          activePodcastPlayer = { ...emptyPodcastPlayer };
          activePodcastId = '';
          activeEpisodeId = '';
        });
        if (enabled('adbKiosk')) fetch('/api/kiosk', { method: 'POST' }).catch(() => {});
      })
      .catch(() => {
        if (enabled('spotify')) void initSpotifyWebPlayer();
      });
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
  let singlePage = $state(false);

  function syncPageLayout() {
    singlePage = isPortraitViewport();
  }

  function pageWidth() {
    const first = pagesEl?.firstElementChild as HTMLElement | undefined;
    return first?.getBoundingClientRect().width || pagesEl?.clientWidth || 0;
  }

  function visiblePageCount() {
    if (!pagesEl) return 1;
    const width = pageWidth();
    if (width <= 0) return 1;
    return Math.max(1, Math.round(pagesEl.clientWidth / width));
  }

  /** Rotate children so the first visible page is first and scroll is 0.
   *  scroll-to-now-playing leaves the carousel scrolled to the LYD page;
   *  advance() assumes position 0. */
  function normalizeCarousel() {
    if (!pagesEl) return;
    const width = pageWidth();
    if (width <= 0) return;
    const firstVisible = Math.round(pagesEl.scrollLeft / width);
    for (let i = 0; i < firstVisible && pagesEl.children.length > 1; i++) {
      pagesEl.appendChild(pagesEl.firstElementChild as HTMLElement);
    }
    if (firstVisible > 0) pagesEl.scrollTo({ left: 0, behavior: 'instant' });
  }

  /** Step a whole screen (all visible columns) in either direction. The
   *  carousel is a ring: pages are rotated in the DOM so scroll is always 0
   *  when idle, and stepping wraps around. */
  function advance(direction: 1 | -1 = 1) {
    if (advancing || !pagesEl || pagesEl.children.length < 2) return;
    normalizeCarousel();
    const width = pageWidth();
    const step = Math.min(visiblePageCount(), pagesEl.children.length - 1);
    if (width <= 0 || step < 1) return;
    advancing = true;

    function finish() {
      pagesEl.removeEventListener('scrollend', finish);
      if (direction > 0) {
        // Forward: the pages we scrolled past go to the back of the ring.
        for (let i = 0; i < step; i++) pagesEl.appendChild(pagesEl.firstElementChild as HTMLElement);
      }
      pagesEl.scrollTo({ left: 0, behavior: 'instant' });
      advancing = false;
    }

    if (direction > 0) {
      pagesEl.scrollTo({ left: width * step, behavior: 'smooth' });
    } else {
      // Backward: pull the last pages to the front, jump onto the current
      // screen instantly, then glide back to 0 where the new pages sit.
      for (let i = 0; i < step; i++) pagesEl.prepend(pagesEl.lastElementChild as HTMLElement);
      pagesEl.scrollTo({ left: width * step, behavior: 'instant' });
      requestAnimationFrame(() => pagesEl.scrollTo({ left: 0, behavior: 'smooth' }));
    }
    pagesEl.addEventListener('scrollend', finish, { once: true });
    // Fallback if scrollend doesn't fire (older browsers)
    setTimeout(() => { if (advancing) finish(); }, 700);
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

  let spotifySaved = $state(false);
  let saveLoading = $state(false);
  let radioSaveDone = $state(false);

  // ── Vertical card carousel ──────────────────────────────────────────────
  let lydInner = $state<HTMLDivElement>();
  let lysInner = $state<HTMLDivElement>();
  let solInner = $state<HTMLDivElement>();
  let cardAdvancing = $state(false);

  function advanceCard(el: HTMLDivElement, kind: 'lyd' | 'lys' | 'sol' | 'podcast' | 'playlist') {
    if (cardAdvancing || !el || el.children.length < 2) return;
    if (kind === 'playlist') {
      scrollPlaylistPage(1);
      return;
    }
    cardAdvancing = true;
    // Scroll exactly one card, not the container height: the container has
    // bottom padding for the arrow, so clientHeight overshoots into card 3
    // and the reorder snap-back becomes a visible jump.
    const first = el.children[0] as HTMLElement;
    const second = el.children[1] as HTMLElement;
    const cardH = second.offsetTop - first.offsetTop || first.offsetHeight || el.clientHeight;
    el.scrollTo({ top: cardH, behavior: 'smooth' });

    function onDone() {
      el.removeEventListener('scrollend', onDone);
      const first = el.firstElementChild;
      if (first) el.appendChild(first);
      el.scrollTo({ top: 0, behavior: 'instant' });
      cardAdvancing = false;
    }
    el.addEventListener('scrollend', onDone, { once: true });
    setTimeout(() => { if (cardAdvancing) onDone(); }, 600);
  }


  $effect(() => {
    if (!lydInner) return;
    registerScrollToNowPlaying(() => {
      if (!lydInner) return;
      const lydPage = lydInner.closest('.page') as HTMLElement | null;
      if (pagesEl && lydPage) {
        pagesEl.scrollTo({ left: lydPage.offsetLeft, behavior: 'smooth' });
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
    if (isRadioPlaylistSaveable()) return radioSaveDone ? 'Playliste gemt' : 'Gem playliste';
    if (playlist.playListMode === 'playlist' && playlist.savedPlaylistActive) return 'Playliste gemt';
    return isCurrentTrackSaved() ? 'Sang gemt' : 'Gem sang';
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
  let showPodcastQueue = $state(false);

  // Lists step two rows per arrow tap. The row nearest the top is "current";
  // we land exactly on a row edge so the list never sits half a row off.
  const LIST_STEP_ROWS = 2;

  function scrollListRows(el: HTMLDivElement | undefined, selector: string, direction: 1 | -1) {
    if (!el) return;
    const rows = [...el.querySelectorAll<HTMLElement>(selector)];
    if (!rows.length) return;
    const currentTop = el.scrollTop;
    let current = rows.findIndex((row) => row.offsetTop >= currentTop - 12);
    if (current < 0) current = rows.length - 1;
    const maxTop = el.scrollHeight - el.clientHeight;
    const targetIndex = current + direction * LIST_STEP_ROWS;
    if (targetIndex >= rows.length || (direction > 0 && rows[targetIndex].offsetTop >= maxTop)) {
      el.scrollTo({ top: maxTop, behavior: 'smooth' });
      return;
    }
    el.scrollTo({ top: rows[Math.max(0, targetIndex)].offsetTop, behavior: 'smooth' });
  }

  function scrollPodcastPage(direction: 1 | -1) {
    scrollListRows(podcastInner, '.podcast-card', direction);
  }

  function openPodcastSeek() {
    if (!isPodcastTransport()) return;
    podcastSeekOpen = true;
  }

  function closePodcastSeek() {
    podcastSeekOpen = false;
    seekingPodcast = false;
  }

  // ── Bibliotek: playlister og podcast deler én side ─────────────────────────
  let libraryTab = $state<'playlists' | 'podcasts'>('playlists');
  const libraryView = $derived<'playlists' | 'podcasts'>(
    enabled('playlists') && (libraryTab === 'playlists' || !enabled('podcasts')) ? 'playlists' : 'podcasts'
  );

  function setLibraryTab(tab: 'playlists' | 'podcasts') {
    libraryTab = tab;
  }

  function goToPodcastNow() {
    closePodcastSeek();
    libraryTab = 'podcasts';
    openPodcastQueue();
    if (!pagesEl) return;
    for (let i = 0; i < pagesEl.children.length; i++) {
      const first = pagesEl.firstElementChild as HTMLElement | null;
      if (first?.dataset.page === 'podcast') break;
      if (first) pagesEl.appendChild(first);
    }
    pagesEl.scrollTo({ left: 0, behavior: 'instant' });
  }

  function openPodcastQueue() {
    if (!activePodcastPlayer.active && playlist.podcastQueue.length === 0) return;
    closeDrill();
    showPodcastQueue = true;
    requestAnimationFrame(() => podcastInner?.scrollTo({ top: 0, behavior: 'smooth' }));
  }

  function closePodcastQueue() {
    showPodcastQueue = false;
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

  function adoptPodcastPlayer(player: Partial<PodcastPlayerState> | undefined, push = true) {
    const next = normalizePodcastPlayer(player);
    if (next.active) {
      activePodcastPlayer = next;
      activePodcastId = next.showId;
      activeEpisodeId = next.episodeId;
      setPodcastTransportFromPlayer(next as unknown as Record<string, unknown>, push);
      return;
    }
    const wasPodcast = activePodcastPlayer.active || playlist.activeTransport === 'podcast';
    activePodcastPlayer = next;
    activePodcastId = '';
    activeEpisodeId = '';
    if (wasPodcast) clearPodcastTransport(push);
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
    if (data?.player) adoptPodcastPlayer(data.player as Partial<PodcastPlayerState>);
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
    if (activePodcastId === showId) {
      await togglePodcastPlayPause();
      return;
    }
    loadingPodcastId = showId;
    try {
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
        if (data.player) adoptPodcastPlayer(data.player as Partial<PodcastPlayerState>);
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
    if (activeEpisodeId === ep.id) {
      await togglePodcastPlayPause();
      return;
    }
    loadingEpisodeId = ep.id;
    try {
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
        if (data.player) adoptPodcastPlayer(data.player as Partial<PodcastPlayerState>);
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
    // The physical backend is authoritative. Firestore can briefly retain a
    // completed podcast transport and must not turn the music Play button into
    // a no-op after the backend has marked that podcast inactive.
    return activePodcastPlayer.active;
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
    if (isPodcastTransport()) {
      const speaker = speakerNowPlaying();
      if (speaker?.name) {
        return {
          title: speaker.name,
          artist: speaker.artist || activePodcastPlayer.showTitle || playlist.podcastShowTitle || '',
          fromSpeaker: true,
        };
      }
      return {
        title: activePodcastPlayer.episodeTitle || playlist.podcastEpisodeTitle || 'Podcast',
        artist: activePodcastPlayer.showTitle || playlist.podcastShowTitle || '',
        fromSpeaker: false,
      };
    }
    if (playlist.spotifyTitle) {
      return { title: playlist.spotifyTitle, artist: playlist.spotifyArtist || '', fromSpeaker: false };
    }
    return { title: '', artist: '', fromSpeaker: false };
  }

  function liveIsPlaying() {
    if (isPodcastTransport()) return activePodcastPlayer.playing;
    return playlist.spotifyPlaying;
  }

  async function toggleNpPlayback() {
    if (isPodcastTransport()) {
      await togglePodcastPlayPause();
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

  $effect(() => {
    if (playlist.playListMode !== 'playlist') activePlaylistId = '';
  });

  type PlaylistTrack = { uri: string; name: string; artist: string; position?: number };
  let drilledPlaylist = $state<RadioPlaylist | null>(null);
  let drilledTracks = $state<PlaylistTrack[]>([]);

  function startCachedPlaylist(p: RadioPlaylist) {
    playlist.spotifyRadio = false;
    playlist.spotifyAlbumActive = false;
    playlist.savedPlaylistActive = true;
    playlist.savedPlaylistTitle = p.name;
    playlist.savedPlaylistQueue = p.tracks;
    playlist.savedPlaylistIndex = 0;
    playlist.playListMode = 'playlist';
    paintNpFromQueues();
  }

  function scrollPlaylistPage(direction: 1 | -1) {
    scrollListRows(playlistInner, '.playlist-card', direction);
  }

  async function playSpotifyPlaylist(p: RadioPlaylist) {
    if (loadingPlaylistId) return;
    loadingPlaylistId = p.id;
    try {
      await releasePodcastForMusic();
      startCachedPlaylist(p);
      activePlaylistId = p.id;
      await playFromCurrentIndex();
    } finally {
      loadingPlaylistId = '';
    }
  }

  function openPlaylistDrill(p: RadioPlaylist) {
    drilledPlaylist = p;
    drilledTracks = p.tracks.map((track, i) => ({ ...track, position: i }));
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
      if (activePlaylistId === drilledPlaylist.id && playlist.savedPlaylistQueue.length > 0) {
        playlist.savedPlaylistIndex = index;
        paintNpFromQueues();
      } else {
        startCachedPlaylist(drilledPlaylist);
        activePlaylistId = drilledPlaylist.id;
        playlist.savedPlaylistIndex = index;
        paintNpFromQueues();
      }
      await playFromCurrentIndex();
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

<svelte:window
  onkeydown={(e) => { if (e.key === 'Escape') closePodcastSeek(); }}
  onresize={() => {
    syncPageLayout();
    pagesEl?.scrollTo({ left: 0, behavior: 'instant' });
  }}
/>

<main class:single-page={singlePage}>
  <FeedbackOverlay />

  <!-- Splash screen for fullscreen entry -->
  {#if showSplash}
    <div class="splash" onclick={dismissSplash} role="button" tabindex="0" onkeydown={(e) => e.key === 'Enter' && dismissSplash()}>
      <span class="splash-title">{store.config.site === 'garden' ? 'HAVEN' : 'EJDERSTED'}</span>
    </div>
  {/if}

  <!-- Dim overlay + clock: clock lives inside so the tap target is the overlay. -->
  <div
    class="dim-overlay"
    class:dimmed
    role="button"
    tabindex="-1"
    aria-label="Væk kiosk"
    onpointerdown={() => noteActivity(true)}
    onclick={() => noteActivity(true)}
    onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') noteActivity(true); }}
  >
    {#if dimmed}
      <div class="clock">{clockTime}</div>
    {/if}
  </div>

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

  <!-- ── Page arrows: one screen (all visible columns) per tap ─────────────── -->
  <div class="page-nav">
    <button class="advance-arrow" onclick={() => advance(-1)} aria-label="Forrige side">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="15 6 9 12 15 18" />
      </svg>
    </button>
    <button class="advance-arrow" onclick={() => advance(1)} aria-label="Næste side">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="9 6 15 12 9 18" />
      </svg>
    </button>
  </div>

  <!-- ── Swipe container ───────────────────────────────────────────────────── -->
  <div class="pages" bind:this={pagesEl}>

    {#if enabled('camera') && store.config.site === 'garden'}
      <!-- PAGE · KAMERA (garden-first) ────────────────────────────────────── -->
      <section class="page page--primary-camera">
        <div class="col-header" aria-hidden="true"></div>
        <div class="scroll-inner camera-page">
          <CameraCard />
        </div>
      </section>
    {/if}

    <!-- PAGE · SOL (Fossibot + garden solar charge relay) ───────────────── -->
    {#if enabled('solar') || enabled('fossibot')}
    <section class="page">
      <div class="col-header" aria-hidden="true"></div>
      <div class="scroll-inner" bind:this={solInner}>
        {#if enabled('fossibot')}
          <FossibotCard />
        {/if}
        {#if enabled('solar')}
        <!-- Header carries the relay state; the active button carries the mode.
             The window is sunrise+offset → sunset−offset, so the raw sun times add nothing. -->
        <Card
          name="Solcelle"
          status={store.solar.relayOn ? 'tilsluttet' : 'afbrudt'}
          online={!!store.solar.relayOn}
        >
          <div class="solar">
            <div class="solar-window" class:muted={store.solar.mode !== 'auto'} aria-label="Automatisk tidsrum">
              <span class="solar-sched-time">{store.solar.onTime ?? '–'}</span>
              <span class="solar-window-dash">–</span>
              <span class="solar-sched-time">{store.solar.offTime ?? '–'}</span>
            </div>

            <div class="solar-modes" role="group" aria-label="Solcelle-styring">
              <button type="button" class="action-btn" class:active={store.solar.mode === 'on'} onclick={() => store.setSolarMode('on')}>tænd</button>
              <button type="button" class="action-btn" class:active={store.solar.mode === 'auto'} onclick={() => store.setSolarMode('auto')}>auto</button>
              <button type="button" class="action-btn" class:active={store.solar.mode === 'off'} onclick={() => store.setSolarMode('off')}>sluk</button>
            </div>
          </div>
        </Card>
        {/if}
        {#if store.config.switchbot?.configured}
          <SwitchbotCard />
        {/if}
      </div>
      {#if [enabled('fossibot'), enabled('solar'), !!store.config.switchbot?.configured].filter(Boolean).length > 1}
      <button type="button" class="card-arrow" onclick={() => solInner && advanceCard(solInner, 'sol')} aria-label="Næste kort">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {/if}
    </section>
    {/if}

    <!-- PAGE 0 · LYD ─────────────────────────────────────────────────────── -->
    {#if enabled('audio') || enabled('spotify')}
    <section class="page">
      <div class="col-header" aria-hidden="true"></div>
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
                  aria-label={currentSaveLabel()}
                  title={currentSaveLabel()}
                >
                  <!-- Glyph, not a sentence: filled = saved. The overlay says what was saved. -->
                  <svg viewBox="0 0 24 24" fill={spotifySaved || radioSaveDone ? 'currentColor' : 'none'} stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M6 3h12v18l-6-4.5L6 21z" />
                  </svg>
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
                <button type="button" class="np-next-streamer" onclick={playBrowsedTrack} aria-label="Spring til næste sang">
                  <span class="np-next-line">
                    <span class="np-next-mark" aria-hidden="true">›</span>
                    {playlist.spotifyNextTitle}{#if playlist.spotifyNextArtist}<span class="np-next-artist"> · {playlist.spotifyNextArtist}</span>{/if}
                  </span>
                </button>
              {/if}
            {:else}
              <span class="np-card-title np-card-title--muted">Ingen valgt sang</span>
              <span class="np-card-artist">Brug mikrofonen nedenfor for at tilføje til køen</span>
            {/if}
          </div>
          <div class="unified-vol unified-vol--horizontal np-volume" aria-label="Afspiller-volumen">
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
              name=""
              status=""
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
                        aria-label={`Volumen ${device.name}`}
                        oninput={(e) => {
                          const level = +(e.currentTarget as HTMLInputElement).value;
                          if (!vol.online) {
                            void wakeHouseSpeaker(device.id, device.name, level);
                            return;
                          }
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
            {@const single = audioTargets.length === 1 ? audioTargets[0] : null}
            <Card
              name=""
              status=""
              online={audioTargets.some((target) => target.online)}
            >
              <div class="audio-targets">
                {#each audioTargets as target (target.id)}
                  <div class="audio-target" class:offline={!target.online}>
                    {#if !single}
                    <div class="audio-target-row">
                      <div class="audio-target-main">
                        <span class="audio-target-name">{target.name}</span>
                      </div>
                    </div>
                    {/if}
                    <div class="unified-vol unified-vol--horizontal audio-target-vol">
                      <input
                        type="range"
                        min="0"
                        max="100"
                        step="1"
                        class="unified-vol-slider"
                        value={readTargetVolume(target)}
                        oninput={(e) => {
                          const level = +(e.currentTarget as HTMLInputElement).value;
                          unifiedVolume = level;
                          if (!target.online) {
                            void reconnectAudioTarget(target.id);
                            return;
                          }
                          unifiedDragging = true;
                          queueTargetVolume(target.id, level);
                        }}
                        onchange={(e) => {
                          unifiedDragging = false;
                          if (!target.online) return;
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
        <!-- The card is the input (mic + search). Play mode already shows on the
             player's action row, so the status only carries transient work. -->
        <Card
          name=""
          status={playlist.spotifyRadioLoading
            ? 'Opbygger playliste…'
            : playlist.spotifyAlbumLoading
              ? 'Henter album…'
              : ''}
        >
          <SpotifyVoice onvoice={handleVoicePayload} />
        </Card>
        {/if}

      </div>
      <button type="button" class="card-arrow card-arrow--lyd" onclick={() => lydInner && advanceCard(lydInner, 'lyd')} aria-label="Næste kort">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
    </section>
    {/if}

    <!-- PAGE 1 · BIBLIOTEK (playlister · podcast) ───────────────────────── -->
    {#if enabled('playlists') || enabled('podcasts')}
    <section class="page" data-page="podcast">
      {#if libraryView === 'playlists' && drilledPlaylist}
        <div class="col-header drill-header">
          <button type="button" class="drill-back" onclick={closePlaylistDrill} aria-label="Tilbage til playliste-liste">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="15 6 9 12 15 18" />
            </svg>
            <span class="drill-back-label">{drilledPlaylist.name}</span>
          </button>
        </div>
      {:else if libraryView === 'podcasts' && showPodcastQueue}
        <div class="col-header drill-header">
          <button type="button" class="drill-back" onclick={closePodcastQueue} aria-label="Tilbage til podcast-liste">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="15 6 9 12 15 18" />
            </svg>
            <span class="drill-back-label">Afspiller nu</span>
          </button>
        </div>
      {:else if libraryView === 'podcasts' && drilledShow}
        <div class="col-header drill-header">
          <button type="button" class="drill-back" onclick={closeDrill} aria-label="Tilbage til podcast-liste">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="15 6 9 12 15 18" />
            </svg>
            <span class="drill-back-label">{drilledShow.show_name}</span>
          </button>
        </div>
      {:else}
        <!-- One library page: the header is the switch. -->
        <div class="col-header col-header--tabs" role="tablist">
          {#if enabled('playlists')}
            <button type="button" role="tab" class:active={libraryView === 'playlists'} aria-selected={libraryView === 'playlists'} onclick={() => setLibraryTab('playlists')}>playlister</button>
          {/if}
          {#if enabled('podcasts')}
            <button type="button" role="tab" class:active={libraryView === 'podcasts'} aria-selected={libraryView === 'podcasts'} onclick={() => setLibraryTab('podcasts')}>podcast</button>
          {/if}
        </div>
      {/if}

      {#if libraryView === 'playlists'}
      <div class="scroll-inner list-scroll" bind:this={playlistInner}>
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
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="18 15 12 9 6 15" />
          </svg>
        </button>

        <button type="button" class="card-arrow list-arrow list-arrow--down" onclick={() => scrollPlaylistPage(1)} aria-label="Næste playliste">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      {/if}
      {:else}
      <div class="scroll-inner list-scroll" bind:this={podcastInner}>
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
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="18 15 12 9 6 15" />
          </svg>
        </button>

        <button type="button" class="card-arrow list-arrow list-arrow--down" onclick={() => scrollPodcastPage(1)} aria-label="Næste podcast">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      {/if}
      {/if}
    </section>
    {/if}

    <!-- PAGE 2 · LYS ─────────────────────────────────────────────────────── -->
    {#if enabled('hue') || enabled('lights')}
    <section class="page">
      <div class="col-header" aria-hidden="true"></div>
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
            <Card name={room.name} status="" online={room.any_on}>
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
          <LightsCard />
        {/if}

      </div>
      <button type="button" class="card-arrow card-arrow--lys" onclick={() => lysInner && advanceCard(lysInner, 'lys')} aria-label="Næste kort">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
    </section>
    {/if}

    <!-- PAGE 4 · KAMERA ──────────────────────────────────────────────────── -->
    {#if enabled('camera') && store.config.site !== 'garden'}
    <section class="page">
      <div class="col-header" aria-hidden="true"></div>
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
    touch-action: manipulation;
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
    /* Landscape: the old 12rem floor. Portrait: drop it — 12rem × ~2.75
       overflows a 390–720px page. */
    font-size: clamp(12rem, 38vw, 28rem);
    font-weight: 300;
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
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

  .col-header--tabs {
    gap: 26px;
  }
  .col-header--tabs button {
    background: none;
    border: none;
    padding: 0;
    font: inherit;
    letter-spacing: inherit;
    text-transform: inherit;
    color: #5a5a5a;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: color 0.2s;
  }
  .col-header--tabs button.active {
    color: #9b9b9b;
  }
  .col-header--tabs button:active {
    color: #f2f2f2;
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
    width: 50%;
    min-width: 50%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    position: relative;
  }
  .page--primary-camera {
    flex-basis: 50%;
  }

  /* One card/page when the viewport is taller than wide. Aspect-ratio beats
     max-width: 720px, which also matched the landscape A12 (~712–800 CSS px). */
  @media (orientation: portrait), (max-aspect-ratio: 1/1) {
    .page,
    .page--primary-camera {
      flex: 0 0 100%;
      flex-basis: 100%;
      width: 100%;
      min-width: 100%;
    }
    .clock {
      font-size: min(32vw, 18vh);
    }
  }
  main.single-page .page,
  main.single-page .page--primary-camera {
    flex: 0 0 100%;
    flex-basis: 100%;
    width: 100%;
    min-width: 100%;
  }
  main.single-page .clock {
    font-size: min(32vw, 18vh);
  }

  /* ── Advance arrow ────────────────────────────────────────────────────────── */
  .page-nav {
    position: fixed;
    top: 0;
    right: 0;
    z-index: 10;
    display: flex;
  }
  .advance-arrow {
    height: 48px;
    display: flex;
    align-items: flex-end;
    padding: 0 12px 10px 12px;
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
    min-height: 0;
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
    flex: 1 1 auto;
    min-height: 142px;
    max-height: 160px;
    align-self: center;
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
    touch-action: none;
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

  .solar-window {
    display: flex;
    align-items: baseline;
    gap: 14px;
    transition: opacity 0.3s ease;
  }

  .solar-window.muted {
    opacity: 0.35;
  }

  .solar-sched-time {
    color: #f2f2f2;
    font-size: 2.4rem;
    font-weight: 200;
    letter-spacing: 0.04em;
    font-variant-numeric: tabular-nums;
    line-height: 1;
  }

  .solar-window-dash {
    color: #9b9b9b;
    font-size: 1.4rem;
    font-weight: 200;
    line-height: 1;
  }

  .solar-modes {
    display: flex;
    gap: 6px;
  }

  @media (max-width: 932px) {
    .solar {
      gap: 8px;
      padding: 6px 4px;
    }
    .solar-window {
      gap: 10px;
    }
    .solar-sched-time {
      font-size: 1.7rem;
    }
    .solar-window-dash {
      font-size: 1rem;
    }
  }

  /* ── Now Playing card ────────────────────────────────────────────────────── */
  .np-card {
    flex: 0 0 100%;
    height: 100%;
    min-height: 100%;
    max-height: 100%;
    overflow: hidden;
    display: grid;
    grid-template-rows: minmax(0, 1fr) 42px 34px;
    align-items: center;
    justify-items: center;
    gap: 14px;
    /* The card-arrow lives in .scroll-inner's own bottom padding, so the card
       only needs a hair of clearance — not a second 62 px reservation. */
    padding: 8px 24px 14px;
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
    touch-action: pan-x;
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
    display: flex;
    align-items: center;
    justify-content: center;
    background: none;
    border: none;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    padding: 10px 14px;
    color: #8a8a8a;
    transition: color 0.15s;
  }
  .np-save-btn svg {
    width: 18px;
    height: 18px;
    display: block;
  }
  .np-save-btn.loading {
    animation: pulse-dim 1.2s ease-in-out infinite;
  }
  .np-save-btn:active {
    color: #f2f2f2;
  }
  .np-save-btn.saved {
    color: #0080c8;
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
    padding: 0;
    border: 0;
    background: none;
    width: 100%;
    font: inherit;
    color: inherit;
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
  /* Next track: one muted line under the title, tap to skip to it. */
  .np-next-line {
    font-size: 0.7rem;
    font-weight: 300;
    color: #9b9b9b;
    text-align: center;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }

  .np-next-mark {
    margin-right: 6px;
    color: #6f6f6f;
  }

  .np-next-artist {
    color: #6f6f6f;
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
