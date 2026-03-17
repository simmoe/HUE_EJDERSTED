<script lang="ts">
  import { onMount } from 'svelte';
  import { store } from '$lib/ws.svelte';
  import VolumeKnob from '$lib/VolumeKnob.svelte';

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

  function resetDim() {
    dimmed = false;
    clearTimeout(dimTimer);
    dimTimer = setTimeout(() => { dimmed = true; }, 30_000);
  }

  // ── Fullscreen splash ───────────────────────────────────────────────────────
  let showSplash = $state(true);

  function dismissSplash() {
    document.documentElement.requestFullscreen?.().catch(() => {});
    requestWakeLock();
    showSplash = false;
    resetDim();
  }

  onMount(() => {
    store.connect();
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') requestWakeLock();
    });
    for (const ev of ['pointerdown', 'pointermove', 'keydown'] as const) {
      document.addEventListener(ev, () => { resetDim(); }, { passive: true });
    }
  });

  // ── Swipe navigation ────────────────────────────────────────────────────────
  let pagesEl: HTMLDivElement;
  let activePage = $state(0);

  function onScroll() {
    if (!pagesEl) return;
    activePage = Math.round(pagesEl.scrollLeft / pagesEl.clientWidth);
  }

  function goTo(i: number) {
    pagesEl?.scrollTo({ left: i * pagesEl.clientWidth, behavior: 'smooth' });
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
    const m = hueMuteState[roomId];
    if (m?.muted) {
      hueMuteState[roomId] = { muted: false, prev: m.prev };
      store.setHueBrightness(roomId, m.prev || 50);
    } else {
      hueMuteState[roomId] = { muted: true, prev: currentBrightness };
      store.setHueBrightness(roomId, 0);
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

  <!-- Song streamer (above dim) -->
  {#if streamer}
    <div class="streamer">
      <span class="streamer-title">{streamer.title}</span>
      {#if streamer.artist}<span class="streamer-artist">{streamer.artist}</span>{/if}
    </div>
  {/if}

  <!-- ── Tab-nav ────────────────────────────────────────────────────────────── -->
  <nav>
    <button class:active={activePage === 0} onclick={() => goTo(0)}>LYD</button>
    <button class:active={activePage === 1} onclick={() => goTo(1)}>LYS</button>
    {#if !store.connected}
      <span class="conn">•</span>
    {/if}
  </nav>

  <!-- ── Swipe container ───────────────────────────────────────────────────── -->
  <div class="pages" bind:this={pagesEl} onscroll={onScroll}>

    <!-- PAGE 0 · LYD ─────────────────────────────────────────────────────── -->
    <section class="page">
      <div class="scroll-inner">
        {#each store.devices as device (device.id)}
          {@const vol = store.volumes[device.id] ?? { level: 0, online: false }}
          <article class="card" class:pulse={pulsingDevices[device.id]}>
            <div class="card-top">
              <span class="card-name">{device.name}</span>
              <span class="card-status" class:online={vol.online}>
                {vol.online ? 'online' : 'offline'}
              </span>
            </div>
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
          </article>
        {/each}

        {#if store.devices.length === 0 && store.connected}
          <p class="empty">Ingen højttalere fundet.</p>
        {/if}
      </div>
    </section>

    <!-- PAGE 1 · LYS ─────────────────────────────────────────────────────── -->
    <section class="page">
      <div class="scroll-inner">

        {#if store.hueStatus.paired && store.hueRooms.length > 0}
          <!-- Rum-knobs -->
          {#each store.hueRooms as room (room.id)}
            <article class="card">
              <div class="card-top">
                <span class="card-name">{room.name}</span>
                <span class="card-status" class:online={room.any_on}>
                  {room.any_on ? 'tændt' : 'slukket'}
                </span>
              </div>
              <div class="knob-wrap">
                <VolumeKnob
                  value={hueMuteState[room.id]?.muted ? 0 : room.brightness}
                  muted={hueMuteState[room.id]?.muted ?? false}
                  disabled={hueMuteState[room.id]?.muted ?? false}
                  onchange={(v) => store.setHueBrightness(room.id, v)}
                  onmute={() => toggleHueMute(room.id, room.brightness)}
                />
              </div>
            </article>
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
    opacity: 0.92;
    pointer-events: auto;
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

  /* ── Nav ──────────────────────────────────────────────────────────────────── */
  nav {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: 24px;
    padding: 28px 28px 0;
  }

  nav button {
    background: none;
    border: none;
    font-size: 0.7rem;
    font-weight: 400;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: #595959;
    cursor: pointer;
    padding: 0;
    transition: color 0.2s;
  }
  nav button.active { color: #ebebeb; }

  .conn {
    margin-left: auto;
    font-size: 0.65rem;
    color: #595959;
    animation: fade 1.4s ease-in-out infinite alternate;
  }
  @keyframes fade { from { opacity: 1 } to { opacity: 0.2 } }

  /* ── Swipe pages ──────────────────────────────────────────────────────────── */
  .pages {
    flex: 1;
    display: flex;
    overflow-x: scroll;
    overflow-y: hidden;
    scroll-snap-type: x mandatory;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
  }
  .pages::-webkit-scrollbar { display: none; }

  .page {
    flex: 0 0 100%;
    scroll-snap-align: start;
    overflow-y: auto;
    overflow-x: hidden;
  }

  .scroll-inner {
    display: flex;
    flex-direction: column;
    gap: 16px;
    padding: 24px 20px calc(env(safe-area-inset-bottom, 0px) + 32px);
    max-width: 480px;
    margin: 0 auto;
    width: 100%;
  }

  /* ── Cards ────────────────────────────────────────────────────────────────── */
  .card {
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 28px;
    padding: 24px 24px 18px;
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    transition: border-color 1.2s ease;
  }

  .card.pulse {
    border-color: rgba(0, 128, 200, 0.35);
    transition: border-color 0.15s ease;
  }

  .card-top {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 8px;
  }

  .card-name {
    font-size: 0.95rem;
    font-weight: 400;
    color: #ebebeb;
  }

  .card-status {
    font-size: 0.65rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #595959;
    transition: color 0.3s;
  }
  .card-status.online { color: #0080c8; }

  .knob-wrap {
    max-width: 280px;
    margin: 0 auto;
  }

  /* ── Now playing ────────────────────────────────────────────────────────────── */
  .now-playing {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    margin-top: 2px;
    padding: 0 8px 4px;
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
    padding-top: 20px;
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

  /* ── Empty state ──────────────────────────────────────────────────────────── */
  .empty {
    text-align: center;
    color: #595959;
    font-size: 0.85rem;
    line-height: 1.6;
    padding: 40px 0;
  }

  /* ── Landscape (kiosk) ───────────────────────────────────────────────────── */
  @media (orientation: landscape) {
    nav {
      display: none;
    }

    /* Disable swipe — show both pages side by side */
    .pages {
      overflow-x: hidden;
      scroll-snap-type: none;
      gap: 0;
    }

    .page {
      flex: 0 0 50%;
    }

    .scroll-inner {
      max-width: none;
      padding: 12px 20px 20px;
      gap: 12px;
    }

    .card {
      padding: 16px 20px 12px;
      border-radius: 22px;
    }

    .knob-wrap {
      max-width: 200px;
    }

    .pair-wrap {
      padding-top: 12px;
    }
  }
</style>
