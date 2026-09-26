<script lang="ts">
  import { onMount } from 'svelte';
  import Card from '$lib/Card.svelte';
  import { store } from '$lib/ws.svelte';

  const watts = (value: number | null | undefined) =>
    value == null || Number.isNaN(value) ? '–' : String(Math.round(value));

  const socParts = (value: number | null | undefined) => {
    if (value == null || Number.isNaN(value)) return null;
    const tenths = Math.round(value * 10);
    return { whole: Math.trunc(tenths / 10), tenth: Math.abs(tenths % 10) };
  };

  const charging = () =>
    !!store.fossibot.online &&
    (!!store.fossibot.charging || (store.fossibot.inWatts ?? store.fossibot.solarWatts ?? 0) > 0);

  // The readings prove it is online; the header only says what the battery is doing.
  const status = () => {
    if (!store.fossibot.online) return 'offline';
    if (charging()) return 'lader';
    if ((store.fossibot.outWatts ?? 0) > 0) return 'aflader';
    return '';
  };

  // Horseshoe: 7 o'clock → clockwise → 5 o'clock (gap at the bottom).
  const TICKS = 40;
  const ARC_START = 210;
  const ARC_SWEEP = 300;
  const CHASE_S = 2.8;

  const tickAngle = (i: number) => ARC_START + (i * ARC_SWEEP) / (TICKS - 1);
  const tickDelay = (i: number) => -((TICKS - i) / TICKS) * CHASE_S;

  const soc = $derived(socParts(store.fossibot.socPercent));

  // Android drops the SVG opacity chase while the dim overlay covers the page
  // and does not resume it when the overlay fades. Toggling the class restarts it.
  let chaseOn = $state(true);
  onMount(() => {
    const restart = () => {
      chaseOn = false;
      requestAnimationFrame(() => {
        chaseOn = true;
      });
    };
    window.addEventListener('kiosk-wake', restart);
    return () => window.removeEventListener('kiosk-wake', restart);
  });
</script>

<Card name="" status={status()} online={!!store.fossibot.online}>
  <div class="fossibot">
    <div class="fossibot-io">
      <div class="fossibot-col">
        <span class="fossibot-label">ind</span>
        <span class="fossibot-reading">
          <span class="fossibot-value">{watts(store.fossibot.inWatts ?? store.fossibot.solarWatts)}</span>
          <span class="fossibot-unit">W</span>
        </span>
      </div>

      <div class="fossibot-dial" class:charging={charging() && chaseOn} class:online={!!store.fossibot.online}>
        <svg class="fossibot-dial-ring" viewBox="0 0 100 100" aria-hidden="true">
          {#each Array.from({ length: TICKS }, (_, i) => i) as i}
            <g transform="rotate({tickAngle(i)} 50 50)">
              <rect
                class="fossibot-tick"
                x="49.1"
                y="4.6"
                width="1.8"
                height="9.3"
                style="animation-delay: {tickDelay(i)}s"
              />
            </g>
          {/each}
        </svg>
        <span class="fossibot-soc" aria-label={soc ? `${soc.whole},${soc.tenth} procent` : 'ukendt'}>
          <span class="fossibot-value fossibot-value--soc">{soc ? soc.whole : '–'}</span>
          {#if soc}
            <span class="fossibot-soc-frac">
              <span class="fossibot-soc-tenth">.{soc.tenth}</span>
              <span class="fossibot-unit fossibot-unit--soc">%</span>
            </span>
          {/if}
        </span>
      </div>

      <div class="fossibot-ports" aria-label="Porte">
          <span class="fossibot-port" class:on={(store.fossibot.solarWatts ?? 0) > 0} title="Sol">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true">
              <circle cx="12" cy="12" r="3.2" />
              <path d="M12 2.6v2.3M12 19.1v2.3M2.6 12h2.3M19.1 12h2.3" />
              <path d="M5.5 5.5l1.6 1.6M16.9 16.9l1.6 1.6M18.5 5.5l-1.6 1.6M7.1 16.9l-1.6 1.6" />
            </svg>
            <span>sol</span>
          </span>
          <span class="fossibot-port" class:on={store.fossibot.usbOn} title="USB">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M8 7V3h8v4" />
              <path d="M7 7h10v8a5 5 0 0 1-10 0V7z" />
              <path d="M12 15v4" />
              <path d="M9 19h6" />
            </svg>
            <span>usb</span>
          </span>
          <span class="fossibot-port" class:on={store.fossibot.dcOn} title="12 V">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <rect x="8" y="4" width="8" height="11" rx="1.4" />
              <path d="M12 15v4" />
              <path d="M9 19h6" />
            </svg>
            <span>12</span>
          </span>
          <span class="fossibot-port" class:on={store.fossibot.acOn} title="230 V">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M8 7V3" />
              <path d="M16 7V3" />
              <path d="M7 7h10v5a5 5 0 0 1-10 0V7z" />
              <path d="M12 17v4" />
            </svg>
            <span>230</span>
          </span>
      </div>

      <div class="fossibot-col">
        <span class="fossibot-label">ud</span>
        <span class="fossibot-reading">
          <span class="fossibot-value">{watts(store.fossibot.outWatts)}</span>
          <span class="fossibot-unit">W</span>
        </span>
      </div>
    </div>
  </div>
</Card>

<style>
  .fossibot {
    container-type: size;
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    width: 100%;
    min-height: 0;
    padding: 8px 4px 12px;
  }

  .fossibot-io {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
    grid-template-rows: auto auto;
    align-items: center;
    justify-items: center;
    width: 100%;
    max-width: 880px;
    column-gap: clamp(40px, 9cqw, 96px);
    row-gap: 22px;
    padding-inline: clamp(8px, 3cqw, 36px);
  }

  .fossibot-col:first-child {
    grid-column: 1;
    grid-row: 1;
  }

  .fossibot-dial {
    grid-column: 2;
    grid-row: 1;
  }

  .fossibot-col:last-child {
    grid-column: 3;
    grid-row: 1;
  }

  .fossibot-ports {
    grid-column: 2;
    grid-row: 2;
  }

  .fossibot-col {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
  }

  .fossibot-label {
    color: #9b9b9b;
    font-size: 0.66rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
  }

  .fossibot-reading {
    display: flex;
    align-items: baseline;
    gap: 6px;
  }

  .fossibot-value {
    color: #f2f2f2;
    font-size: clamp(2rem, 7vw, 3.2rem);
    font-weight: 200;
    letter-spacing: 0.02em;
    font-variant-numeric: tabular-nums;
    line-height: 1;
  }

  .fossibot-unit {
    color: #9b9b9b;
    font-size: 0.72rem;
    letter-spacing: 0.16em;
    text-transform: uppercase;
  }

  .fossibot-dial {
    position: relative;
    display: grid;
    place-items: center;
    aspect-ratio: 1;
    container-type: size;
    /* Viewport, not the card. A size-container height collapses on Android
       and the ticks land on the glyphs. 40vh is the hole in the reference. */
    width: min(40vh, 320px);
    height: min(40vh, 320px);
  }

  .fossibot-soc {
    position: relative;
    display: grid;
    place-items: center;
    z-index: 1;
  }

  .fossibot-value--soc {
    font-size: 28cqmin;
    text-size-adjust: 100%;
  }

  .fossibot-soc-frac {
    position: absolute;
    top: calc(100% + 0.22em);
    left: 50%;
    display: flex;
    align-items: baseline;
    gap: 2px;
    transform: translateX(-50%);
    white-space: nowrap;
    pointer-events: none;
  }

  .fossibot-soc-tenth,
  .fossibot-unit--soc {
    color: #9b9b9b;
    font-size: 0.62rem;
    font-weight: 300;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    font-variant-numeric: tabular-nums;
    line-height: 1;
  }

  .fossibot-dial-ring {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.5s ease;
  }

  .fossibot-dial.online .fossibot-dial-ring {
    opacity: 1;
  }

  .fossibot-tick {
    fill: rgba(255, 255, 255, 0.16);
  }

  .fossibot-dial.charging .fossibot-tick {
    fill: rgba(0, 128, 200, 0.95);
    animation: fossibot-tick-chase 2.8s linear infinite;
  }

  @keyframes fossibot-tick-chase {
    0%,
    100% {
      opacity: 0.18;
    }
    6% {
      opacity: 1;
    }
    18% {
      opacity: 0.22;
    }
  }

  .fossibot-ports {
    position: static;
    display: flex;
    gap: 18px;
    transform: none;
  }

  .fossibot-port {
    display: flex;
    align-items: center;
    gap: 7px;
    color: #555;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    font-size: 0.62rem;
    font-weight: 300;
    transition: color 0.4s ease;
  }

  .fossibot-port svg {
    width: 18px;
    height: 18px;
  }

  .fossibot-port.on {
    color: var(--accent);
  }

  @media (max-width: 932px) {
    .fossibot {
      padding: 4px 2px 8px;
    }
    .fossibot-io {
      column-gap: clamp(28px, 7cqw, 48px);
      row-gap: 16px;
      max-width: 100%;
    }
    .fossibot-col {
      gap: 4px;
    }
    .fossibot-value:not(.fossibot-value--soc) {
      font-size: clamp(1.25rem, 5.5vw, 1.9rem);
    }
    .fossibot-ports {
      gap: 10px;
    }
    .fossibot-soc-tenth,
    .fossibot-unit--soc,
    .fossibot-port {
      font-size: 0.52rem;
    }
    .fossibot-port {
      gap: 4px;
    }
    .fossibot-port svg {
      width: 14px;
      height: 14px;
    }
  }
</style>
