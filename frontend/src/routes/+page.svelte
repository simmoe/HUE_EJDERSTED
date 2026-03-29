<script lang="ts">
  import { onMount } from 'svelte';
  import { store } from '$lib/ws.svelte';
  import Card from '$lib/Card.svelte';
  import VolumeKnob from '$lib/VolumeKnob.svelte';
  import SpotifyVoice from '$lib/SpotifyVoice.svelte';
  import CameraCard from '$lib/CameraCard.svelte';

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
  }

  onMount(() => {
    store.connect();
    updateClock();
    clockInterval = setInterval(updateClock, 1000);
    // Re-apply kiosk settings (immersive mode, landscape) on every page load
    setTimeout(() => fetch('/api/kiosk', { method: 'POST' }).catch(() => {}), 1500);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        requestWakeLock();
        fetch('/api/kiosk', { method: 'POST' }).catch(() => {});
      }
    });
    // Only reset dim on actual screen touches — NOT keydown (volume button is held by case)
    document.addEventListener('pointerdown', () => { if (!showSplash) resetDim(); }, { passive: true });
    return () => clearInterval(clockInterval);
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

  let spotifyTitle = $state('');
  let spotifyArtist = $state('');
  let spotifyRadio = $state(false);
  let spotifyPlaying = $state(false);
  let spotifyNextTitle = $state('');
  let spotifyNextArtist = $state('');

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

  function scrollToNowPlaying() {
    if (!lydInner) return;
    // Cycle DOM until .np-card is first child
    while (lydInner.firstElementChild && !lydInner.firstElementChild.classList.contains('np-card')) {
      lydInner.appendChild(lydInner.firstElementChild);
    }
    lydInner.scrollTo({ top: 0, behavior: 'instant' });
  }

  async function togglePlayPause() {
    const endpoint = spotifyPlaying ? '/api/spotify/pause' : '/api/spotify/resume';
    try {
      const r = await fetch(endpoint, { method: 'POST' });
      const data = await r.json();
      if (data.ok) spotifyPlaying = !spotifyPlaying;
    } catch {}
  }

  async function toggleRadio() {
    if (spotifyRadio) {
      try { await fetch('/api/spotify/radio', { method: 'DELETE' }); } catch {}
      spotifyRadio = false;
      return;
    }
    spotifyRadio = true;
    scrollToNowPlaying();
    try {
      const r = await fetch('/api/spotify/radio', { method: 'POST' });
      const data = await r.json();
      if (!data.ok) {
        spotifyRadio = false;
      }
    } catch {
      spotifyRadio = false;
    }
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
            {#if spotifyTitle}
              <span class="np-card-title">{spotifyTitle}</span>
              {#if spotifyArtist}<span class="np-card-artist">{spotifyArtist}</span>{/if}
              {#if spotifyNextTitle}
                <span class="np-card-next">{spotifyNextTitle}</span>
              {/if}
            {/if}
          </div>
          <div class="action-row">
            <button class="action-btn" onclick={togglePlayPause}>
              {spotifyPlaying ? 'pause' : 'play'}
            </button>
            <button class="action-btn" class:active={spotifyRadio} onclick={toggleRadio}>
              radio
            </button>
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
        <Card name="Musik" status={spotifyRadio ? 'Song Radio' : ''} >
          <SpotifyVoice
            bind:npTitle={spotifyTitle}
            bind:npArtist={spotifyArtist}
            bind:nextTitle={spotifyNextTitle}
            bind:nextArtist={spotifyNextArtist}
            bind:isPlaying={spotifyPlaying}
            bind:radioActive={spotifyRadio}
            onresult={scrollToNowPlaying}
          />
        </Card>

      </div>
      <button class="card-arrow" onclick={() => advanceCard(lydInner, 'lyd')} aria-label="Næste kort">
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
        {#if store.hueStatus.paired && store.hueRooms.length > 0}
          <!-- Rum-knobs -->
          {#each store.hueRooms as room (room.id)}
            <Card name={room.name} status={room.any_on ? 'tændt' : 'slukket'} online={room.any_on}>
              <div class="knob-wrap">
                <VolumeKnob
                  value={hueMuteState[room.id]?.muted ? 0 : room.brightness}
                  muted={!room.any_on || (hueMuteState[room.id]?.muted ?? false)}
                  disabled={!room.any_on || (hueMuteState[room.id]?.muted ?? false)}
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
      <button class="card-arrow" onclick={() => advanceCard(lysInner, 'lys')} aria-label="Næste kort">
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

  /* ── Clock (Apple Standby style) ──────────────────────────────────────────── */
  .clock {
    position: fixed;
    inset: 0;
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: center;
    pointer-events: none;
    font-size: clamp(10rem, 30vw, 22rem);
    font-weight: 200;
    letter-spacing: -0.03em;
    color: #fff;
    font-variant-numeric: tabular-nums;
    font-family: 'Roboto', -apple-system, system-ui, sans-serif;
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
  }

  .scroll-inner {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    touch-action: none;
    max-width: none;
    padding: 0;
    gap: 0;
    scrollbar-width: none;
  }
  .scroll-inner::-webkit-scrollbar { display: none; }

  /* ── Card down-arrow ───────────────────────────────────────────────────────────── */
  .card-arrow {
    position: absolute;
    bottom: 18px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 5;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    background: none;
    border: none;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    color: #595959;
    transition: color 0.2s;
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
    justify-content: center;
    gap: 40px;
    padding: 24px 32px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  }

  .np-info {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
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

  .np-card-next {
    font-size: 0.7rem;
    font-weight: 300;
    color: #888888;
    text-align: center;
    margin-top: 16px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
    opacity: 0.6;
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

  .empty {
    text-align: center;
    color: #595959;
    font-size: 0.85rem;
    line-height: 1.6;
    padding: 40px 0;
  }
</style>
