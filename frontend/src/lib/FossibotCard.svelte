<script lang="ts">
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
    (!!store.fossibot.charging || (store.fossibot.solarWatts ?? 0) > 0);

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
</script>

<Card name="Fossibot" status={status()} online={!!store.fossibot.online}>
  <div class="fossibot">
    <div class="fossibot-io">
      <div class="fossibot-col">
        <span class="fossibot-label">ind</span>
        <span class="fossibot-reading">
          <span class="fossibot-value">{watts(store.fossibot.solarWatts)}</span>
          <span class="fossibot-unit">W</span>
        </span>
      </div>

      <div class="fossibot-dial" class:charging={charging()} class:online={!!store.fossibot.online}>
        <svg class="fossibot-dial-ring" viewBox="0 0 100 100" aria-hidden="true">
          {#each Array.from({ length: TICKS }, (_, i) => i) as i}
            <line
              class="fossibot-tick"
              x1="50"
              y1="5.5"
              x2="50"
              y2="13"
              transform="rotate({tickAngle(i)} 50 50)"
              style="animation-delay: {tickDelay(i)}s"
            />
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
        <div class="fossibot-ports" aria-label="Porte">
          <span class="fossibot-port" class:on={store.fossibot.usbOn} title="USB">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M8 7V3h8v4" />
              <path d="M7 7h10v8a5 5 0 0 1-10 0V7z" />
              <path d="M12 15v4" />
              <path d="M9 19h6" />
            </svg>
            <span>usb</span>
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
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    width: 100%;
    padding: 8px 4px 12px;
  }

  .fossibot-io {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    width: 100%;
    max-width: 760px;
    gap: 6px;
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
    width: min(46vw, 280px);
    height: min(46vw, 280px);
  }

  .fossibot-soc {
    position: relative;
    display: grid;
    place-items: center;
    z-index: 1;
    transform: translateY(-24%);
  }

  .fossibot-value--soc {
    font-size: clamp(2.8rem, min(15vw, 22vh), 6.4rem);
  }

  .fossibot-soc-frac {
    position: absolute;
    top: 100%;
    left: 50%;
    display: flex;
    align-items: baseline;
    gap: 3px;
    transform: translateX(-50%);
    margin-top: 0.04em;
    white-space: nowrap;
  }

  .fossibot-soc-tenth,
  .fossibot-unit--soc {
    color: #9b9b9b;
    font-size: clamp(0.7rem, 2.1vw, 0.95rem);
    font-weight: 300;
    letter-spacing: 0.04em;
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
    stroke: rgba(255, 255, 255, 0.16);
    stroke-width: 1.8;
    stroke-linecap: square;
  }

  .fossibot-dial.charging .fossibot-tick {
    animation: fossibot-tick-chase 2.8s linear infinite;
  }

  @keyframes fossibot-tick-chase {
    0%,
    100% {
      stroke: rgba(255, 255, 255, 0.12);
    }
    5% {
      stroke: rgba(0, 128, 200, 0.95);
    }
    16% {
      stroke: rgba(0, 128, 200, 0.3);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .fossibot-dial.charging .fossibot-tick {
      animation: none;
      stroke: rgba(0, 128, 200, 0.45);
    }
  }

  .fossibot-ports {
    position: absolute;
    left: 50%;
    bottom: 2%;
    transform: translate(-50%, 2rem);
    display: flex;
    gap: 18px;
    z-index: 1;
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
      gap: 2px;
      max-width: 100%;
    }
    .fossibot-col {
      gap: 4px;
    }
    .fossibot-value {
      font-size: clamp(1.25rem, 5.5vw, 1.9rem);
    }
    .fossibot-value--soc {
      font-size: clamp(1.7rem, min(11vw, 14vh), 2.8rem);
    }
    .fossibot-dial {
      width: min(38vw, 34vh, 168px);
      height: min(38vw, 34vh, 168px);
    }
    .fossibot-ports {
      gap: 10px;
      transform: translate(-50%, 1.1rem);
    }
    .fossibot-port {
      font-size: 0.52rem;
      gap: 4px;
    }
    .fossibot-port svg {
      width: 14px;
      height: 14px;
    }
  }
</style>
