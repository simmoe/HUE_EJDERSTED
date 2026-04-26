<script lang="ts">
  import { onMount } from 'svelte';
  import { store } from '$lib/ws.svelte';
  import Card from '$lib/Card.svelte';
  import VolumeKnob from '$lib/VolumeKnob.svelte';
  import SpotifyVoice from '$lib/SpotifyVoice.svelte';
  import CameraCard from '$lib/CameraCard.svelte';
  import {
    playlist,
    activeQueue,
    registerScrollToNowPlaying,
    initPlaylistHub,
    togglePlayPause,
    spotifyNextTrack,
    spotifyPreviousTrack,
    toggleRadio,
    playAlbum,
    handleVoicePayload,
  } from '$lib/playlistHub.svelte';
  import { init as initSpotifyWebPlayer } from '$lib/spotifyPlayer.svelte';

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
  let dimmed = $state(false);
  let dimTimer: ReturnType<typeof setTimeout>;

  async function setBrightness(level: number) {
    try { await fetch(`/api/brightness/${level}`, { method: 'PUT' }); } catch {}
  }

  function resetDim() {
    if (dimmed) setBrightness(255);
    dimmed = false;
    clearTimeout(dimTimer);
    dimTimer = setTimeout(() => { dimmed = true; setBrightness(25); }, 30_000);
  }

  // ── Clock ──────────────────────────────────────────────────────────────────
  let clockTime = $state('');
  let clockInterval: ReturnType<typeof setInterval>;

  function updateClock() {
    const now = new Date();
    clockTime = now.toLocaleTimeString('da-DK', { hour: '2-digit', minute: '2-digit', hour12: false });
  }

  // ── Fullscreen splash ───────────────────────────────────────────────────────
  let showSplash = $state(true);

  function dismissSplash() {
    document.documentElement.requestFullscreen?.().catch(() => {});
    requestWakeLock();
    // Trigger ADB kiosk setup (landscape, brightness, volume HUD)
    fetch('/api/kiosk', { method: 'POST' }).catch(() => {});
    showSplash = false;
    resetDim();
    // Efter brugertryk: Web Playback SDK må bruge audio; Chrome bliver Connect-enhed «Ejdersted».
    void initSpotifyWebPlayer();
  }

  let stopPlaylistHub: (() => void) | undefined;

  onMount(() => {
    store.connect();
    updateClock();
    clockInterval = setInterval(updateClock, 1000);
    void initSpotifyWebPlayer();
    void initPlaylistHub().then((stop) => {
      stopPlaylistHub = stop;
    });
    // Re-apply kiosk settings (immersive mode, landscape) on every page load
    setTimeout(() => fetch('/api/kiosk', { method: 'POST' }).catch(() => {}), 1500);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        requestWakeLock();
        fetch('/api/kiosk', { method: 'POST' }).catch(() => {});
        void initSpotifyWebPlayer();
      }
    });
    // Only reset dim on actual screen touches — NOT keydown (volume button is held by case)
    document.addEventListener('pointerdown', () => { if (!showSplash) resetDim(); }, { passive: true });
    return () => {
      clearInterval(clockInterval);
      stopPlaylistHub?.();
    };
  });

  // ── Horizontal page carousel ────────────────────────────────────────────────
  let pagesEl: HTMLDivElement;
  let advancing = $state(false);
  let nextPageName = $state('');

  function readNextPageName() {
    const el = pagesEl?.children[2] as HTMLElement | undefined;
    nextPageName = el?.querySelector('.col-header')?.textContent ?? '';
  }

  function advance() {
    if (advancing || !pagesEl) return;
    advancing = true;
    const pageW = pagesEl.clientWidth / 2;  // each page = 50%
    pagesEl.scrollTo({ left: pageW, behavior: 'smooth' });

    // When scroll finishes: move first page to end, reset scroll instantly
    function onDone() {
      pagesEl.removeEventListener('scrollend', onDone);
      const first = pagesEl.firstElementChild;
      if (first) pagesEl.appendChild(first);
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

  // ── Samlet volumen: én slider styrer alle online B&O-højttalere ──────────────
  let unifiedVolume = $state(40);
  let unifiedDragging = false;

  $effect(() => {
    if (unifiedDragging) return;
    const online = store.devices
      .map((d) => store.volumes[d.id])
      .filter((v) => v?.online);
    if (online.length === 0) return;
    const avg = Math.round(online.reduce((s, v) => s + v.level, 0) / online.length);
    unifiedVolume = avg;
  });

  function setUnifiedVolume(level: number) {
    unifiedVolume = level;
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

  // ── Vertical card carousel ──────────────────────────────────────────────
  let lydInner: HTMLDivElement;
  let lysInner: HTMLDivElement;
  let cardAdvancing = $state(false);
  let nextLydCard = $state('');
  let nextLysCard = $state('');

  function readNextCardName(el: HTMLDivElement): string {
    const child = el?.children[1] as HTMLElement | undefined;
    if (!child) return '';
    return child.querySelector('.card-name')?.textContent ?? child.dataset.name ?? '';
  }

  function advanceCard(el: HTMLDivElement, kind: 'lyd' | 'lys') {
    if (cardAdvancing || !el || el.children.length < 2) return;
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
      else nextLysCard = readNextCardName(el);
    }
    el.addEventListener('scrollend', onDone, { once: true });
    setTimeout(() => { if (cardAdvancing) onDone(); }, 600);
  }

  // Read initial next-names once DOM is ready
  $effect(() => {
    if (pagesEl) readNextPageName();
    if (lydInner) nextLydCard = readNextCardName(lydInner);
    if (lysInner) nextLysCard = readNextCardName(lysInner);
  });

  $effect(() => {
    if (!lydInner) return;
    registerScrollToNowPlaying(() => {
      if (!lydInner) return;
      while (lydInner.firstElementChild && !lydInner.firstElementChild.classList.contains('np-card')) {
        lydInner.appendChild(lydInner.firstElementChild);
      }
      lydInner.scrollTo({ top: 0, behavior: 'instant' });
    });
  });

  async function checkSaved() {
    if (!playlist.spotifyTrackUri) {
      spotifySaved = false;
      return;
    }
    try {
      const r = await fetch(`/api/spotify/is-saved?uri=${encodeURIComponent(playlist.spotifyTrackUri)}`);
      const data = await r.json();
      spotifySaved = !!data.saved;
    } catch {
      spotifySaved = false;
    }
  }

  async function toggleSave() {
    if (!playlist.spotifyTrackUri) return;
    try {
      const r = await fetch('/api/spotify/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uri: playlist.spotifyTrackUri }),
      });
      const data = await r.json();
      if (data.ok) spotifySaved = !!data.saved;
    } catch {}
  }

  let hueManualIp  = $state('');
  let huePairing   = $state(false);
  let huePairError = $state('');

  async function handlePair(e: Event) {
    e.preventDefault();
    huePairing = true;
    huePairError = '';
    const err = await store.pairHue(hueManualIp.trim() || undefined);
    huePairing = false;
    if (err) huePairError = err;
    else hueManualIp = '';
  }
</script>

<main>
  <!-- Splash screen for fullscreen entry -->
  {#if showSplash}
    <div class="splash" onclick={dismissSplash} role="button" tabindex="0" onkeydown={(e) => e.key === 'Enter' && dismissSplash()}>
      <span class="splash-title">EJDERSTED</span>
    </div>
  {/if}

  <!-- Dim overlay -->
  <div class="dim-overlay" class:dimmed></div>

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

    <!-- PAGE 0 · LYD ─────────────────────────────────────────────────────── -->
    <section class="page">
      <div class="col-header">LYD</div>
      <div class="scroll-inner" bind:this={lydInner}>

        <!-- Now Playing (default card, always visible) -->
        <div class="np-card" data-name="Afspiller">
          <div class="np-info">
            {#if playlist.spotifyTitle}
              <span class="np-card-title">{playlist.spotifyTitle}</span>
              {#if playlist.spotifyArtist}<span class="np-card-artist">{playlist.spotifyArtist}</span>{/if}
              {#if activeQueue().length > 1}
                <div class="np-track-nav" role="group" aria-label="Sang">
                  <button type="button" class="np-track-nav-btn" onclick={spotifyPreviousTrack} aria-label="Forrige i køen">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                      <polyline points="15 18 9 12 15 6" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    class="heart-btn"
                    class:saved={spotifySaved}
                    onclick={toggleSave}
                    disabled={!playlist.spotifyTrackUri}
                    aria-label={spotifySaved ? 'Fjern fra liked' : 'Gem sang'}
                  >
                    <svg viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" fill={spotifySaved ? 'currentColor' : 'none'}>
                      <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z" />
                    </svg>
                  </button>
                  <button type="button" class="np-track-nav-btn" onclick={spotifyNextTrack} aria-label="Næste i køen">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                      <polyline points="9 18 15 12 9 6" />
                    </svg>
                  </button>
                </div>
              {:else}
                <div class="np-track-nav np-track-nav--single" aria-hidden="true">
                  <span class="np-track-nav-spacer"></span>
                  <button
                    type="button"
                    class="heart-btn"
                    class:saved={spotifySaved}
                    onclick={toggleSave}
                    disabled={!playlist.spotifyTrackUri}
                    aria-label={spotifySaved ? 'Fjern fra liked' : 'Gem sang'}
                  >
                    <svg viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" fill={spotifySaved ? 'currentColor' : 'none'}>
                      <path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z" />
                    </svg>
                  </button>
                  <span class="np-track-nav-spacer"></span>
                </div>
              {/if}
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
          <div class="action-row">
            <button type="button" class="action-btn" onclick={togglePlayPause}>
              {playlist.spotifyPlaying ? 'pause' : 'play'}
            </button>
            <button type="button" class="action-btn" class:active={playlist.spotifyRadio} class:loading={playlist.spotifyRadioLoading} onclick={toggleRadio} disabled={playlist.spotifyRadioLoading}>
              {playlist.spotifyRadioLoading ? '· · ·' : 'radio'}
            </button>
            <button type="button" class="action-btn" class:active={playlist.spotifyAlbumActive} class:loading={playlist.spotifyAlbumLoading} onclick={playAlbum} disabled={playlist.spotifyAlbumLoading}>
              {playlist.spotifyAlbumLoading ? '· · ·' : 'album'}
            </button>
          </div>
          <div class="unified-vol">
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
              aria-label="Samlet volumen for alle højttalere"
            />
            <span class="unified-vol-value">{unifiedVolume}</span>
          </div>
        </div>

        {#each store.devices as device (device.id)}
          {@const vol = store.volumes[device.id] ?? { level: 0, online: false }}
          <Card name={device.name} status={vol.online ? 'online' : 'offline'} online={vol.online} pulse={pulsingDevices[device.id]}>
            <div class="knob-wrap">
              <VolumeKnob
                value={muteState[device.id]?.muted ? 0 : vol.level}
                muted={muteState[device.id]?.muted ?? false}
                disabled={!vol.online || (muteState[device.id]?.muted ?? false)}
                onchange={(v) => store.setVolume(device.id, v)}
                onmute={() => toggleMute(device.id, vol.level)}
              />
            </div>
            {#if store.nowPlaying[device.id]?.name}
              {@const np = store.nowPlaying[device.id]}
              <div class="now-playing">
                <span class="np-title">{np.name}</span>
                {#if np.artist}<span class="np-artist">{np.artist}</span>{/if}
              </div>
            {/if}
          </Card>
        {/each}

        {#if store.devices.length === 0 && store.connected}
          <p class="empty">Ingen højttalere fundet.</p>
        {/if}

        <!-- Spotify Voice -->
        <Card
          name="Musik"
          status={playlist.spotifyRadioLoading
            ? 'Opbygger radio…'
            : playlist.spotifyAlbumLoading
              ? 'Henter album…'
              : playlist.spotifyRadioError
                ? playlist.spotifyRadioError
                : playlist.spotifyAlbumError
                  ? playlist.spotifyAlbumError
                  : playlist.playListMode === 'radio'
                    ? 'Song Radio (lokal kø)'
                    : playlist.playListMode === 'album'
                      ? 'Album (lokal kø)'
                      : 'Mikrofon-kø'}
        >
          <SpotifyVoice onvoice={handleVoicePayload} />
        </Card>

      </div>
      <button type="button" class="card-arrow card-arrow--lyd" onclick={() => advanceCard(lydInner, 'lyd')} aria-label="Næste kort">
        <span class="arrow-label">{nextLydCard}</span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
    </section>

    <!-- PAGE 1 · LYS ─────────────────────────────────────────────────────── -->
    <section class="page">
      <div class="col-header">LYS</div>
      <div class="scroll-inner" bind:this={lysInner}>
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
              {#if huePairError}<p class="form-error">{huePairError}</p>{/if}
              <button type="submit" class="btn-primary" disabled={huePairing}>
                {huePairing ? '…' : 'par'}
              </button>
            </form>
          </div>
        {/if}

      </div>
      <button type="button" class="card-arrow card-arrow--lys" onclick={() => advanceCard(lysInner, 'lys')} aria-label="Næste kort">
        <span class="arrow-label">{nextLysCard}</span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
    </section>

    <!-- PAGE 2 · KAMERA ──────────────────────────────────────────────────── -->
    <section class="page">
      <div class="col-header">KAMERA</div>
      <div class="scroll-inner camera-page">
        <CameraCard />
      </div>
    </section>

  </div>
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
    color: #595959;
    transition: color 0.3s;
  }

  .splash:active .splash-title {
    color: #ebebeb;
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
    -webkit-text-stroke: 1.35px rgba(174, 174, 174, 0.6);
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
    color: #ebebeb;
    opacity: 0;
    animation: text-fade 5s ease 0.4s both;
  }

  .streamer-artist {
    font-size: 0.7rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #595959;
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
    color: #595959;
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
    color: #595959;
    transition: color 0.2s;
  }
  .advance-arrow:active { color: #ebebeb; }
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

  /* ── Card down-arrow (fixed — klemmer ikke kortene; én pr. viewport-halv) ───── */
  .card-arrow {
    position: fixed;
    bottom: 0;
    z-index: 6;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    gap: 2px;
    width: 50dvw;
    margin: 0;
    padding: 18px 0 max(18px, env(safe-area-inset-bottom, 0px));
    background: var(--black);
    border: none;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    color: #595959;
    transition: color 0.2s;
    pointer-events: auto;
  }
  .card-arrow--lyd {
    left: 0;
  }
  .card-arrow--lys {
    left: 50dvw;
  }
  .card-arrow:active { color: #ebebeb; }
  .card-arrow svg {
    width: 18px;
    height: 18px;
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
  }

  .camera-page {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px 32px;
  }

  /* ── Now playing ──────────────────────────────────────────────────────────── */
  .now-playing {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    margin-top: 2px;
    padding: 0 8px 4px;
    align-self: start;
  }

  .np-title {
    font-size: 0.8rem;
    color: #ebebeb;
    text-align: center;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 100%;
  }

  .np-artist {
    font-size: 0.65rem;
    letter-spacing: 0.06em;
    color: #595959;
    text-align: center;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 100%;
  }

  /* ── Now Playing card ────────────────────────────────────────────────────── */
  .np-card {
    min-height: calc(100dvh - 48px);
    max-height: calc(100dvh - 48px);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    align-items: center;
    /* flex-start: kompakt midte, luft under action-knapper mod bundpil */
    justify-content: flex-start;
    gap: 18px;
    padding: 16px 24px 12px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  }

  .np-track-nav {
    display: flex;
    flex-direction: row;
    align-items: center;
    justify-content: center;
    gap: 4px;
    margin-top: 2px;
  }

  .np-track-nav-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    background: none;
    border: none;
    padding: 10px 12px;
    margin: 0;
    color: #4a4a4a;
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

  .np-info {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    max-width: 100%;
  }

  .np-card-title {
    font-size: 1.2rem;
    font-weight: 300;
    color: #ebebeb;
    text-align: center;
    letter-spacing: 0.02em;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }

  .np-card-title--muted {
    color: #595959;
  }

  .np-track-nav--single {
    display: flex;
    flex-direction: row;
    align-items: center;
    justify-content: center;
    gap: 4px;
    margin-top: 2px;
  }

  .np-track-nav-spacer {
    width: 44px;
    height: 1px;
    flex-shrink: 0;
  }

  .heart-btn:disabled {
    opacity: 0.35;
    cursor: default;
  }

  .np-card-artist {
    font-size: 0.7rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #595959;
    text-align: center;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }

  .heart-btn {
    background: none;
    border: none;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    padding: 6px 4px;
    margin: 0;
    color: #595959;
    transition: color 0.2s, transform 0.2s;
  }
  .heart-btn svg {
    width: 22px;
    height: 22px;
    transition: fill 0.2s, transform 0.15s;
  }
  .heart-btn:active svg {
    transform: scale(1.3);
  }
  .heart-btn.saved {
    color: #e25555;
  }

  .np-card-next {
    font-size: 0.7rem;
    font-weight: 300;
    color: #888888;
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
    gap: 2px;
    margin-top: 6px;
    opacity: 0;
    animation: streamer-in 0.8s ease 0.2s forwards;
  }

  .np-next-title {
    font-size: 0.8rem;
    font-weight: 300;
    color: #888;
    text-align: center;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }

  .np-next-artist {
    font-size: 0.6rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #4a4a4a;
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
    color: #595959;
    font-size: 0.65rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    cursor: pointer;
    padding: 6px 12px;
    transition: color 0.2s;
  }
  .btn-text:hover { color: #888; }

  .btn-outline {
    display: block;
    width: 100%;
    background: none;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    color: #595959;
    font-size: 0.8rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 16px;
    cursor: pointer;
    transition: border-color 0.2s, color 0.2s;
  }
  .btn-outline:hover { border-color: rgba(255,255,255,0.15); color: #888; }

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
    color: #888;
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
    color: #ebebeb;
    font-size: 0.9rem;
    outline: none;
    transition: border-color 0.2s;
    -webkit-appearance: none;
  }
  form input:focus { border-color: #0080c8; }

  .form-error {
    font-size: 0.75rem;
    color: #888;
    padding: 0 4px;
  }

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
    color: #595959;
  }

  .pair-ip {
    font-size: 1.4rem;
    font-weight: 200;
    color: #ebebeb;
    letter-spacing: 0.04em;
  }

  .pair-hint {
    font-size: 0.85rem;
    color: #595959;
    line-height: 1.6;
  }

  .pair-hint code {
    font-size: 0.78rem;
    color: #7a7a7a;
  }

  .empty {
    text-align: center;
    color: #595959;
    font-size: 0.85rem;
    line-height: 1.6;
    padding: 40px 0;
  }
</style>
