<script lang="ts">
  import Card from '$lib/Card.svelte';
  import { store, type PowerHold, type PowerMode } from '$lib/ws.svelte';

  let expectAc = $state<boolean | null>(null);
  let expectTimer: ReturnType<typeof setTimeout> | null = null;

  const acOn = $derived(expectAc ?? !!store.fossibot.acOn);
  const mode = $derived(store.power.mode ?? 'auto');
  const hold = $derived(store.power.hold);
  const window = $derived(autoWindow(store.solar.sunrise, store.solar.sunset, store.solar.now));

  $effect(() => {
    const live = !!store.fossibot.acOn;
    if (expectAc != null && live === expectAc) settle();
  });

  function settle() {
    expectAc = null;
    if (expectTimer) clearTimeout(expectTimer);
    expectTimer = null;
  }

  function minutes(hm: string | null | undefined): number | null {
    if (!hm) return null;
    const parts = hm.split(/[:.]/).map(Number);
    if (parts.length < 2 || !Number.isFinite(parts[0]) || !Number.isFinite(parts[1])) return null;
    return parts[0] * 60 + parts[1];
  }

  function clockHm(): string {
    if (store.solar.now) return store.solar.now;
    return new Date().toLocaleTimeString('da-DK', { hour: '2-digit', minute: '2-digit', hour12: false });
  }

  function autoWindow(
    sunrise: string | null | undefined,
    sunset: string | null | undefined,
    nowHm?: string | null
  ): { start: string; end: string; startLabel: string; endLabel: string } {
    const nowM = minutes(nowHm ?? clockHm());
    const riseM = minutes(sunrise);
    const setM = minutes(sunset);
    const day = nowM != null && riseM != null && setM != null && nowM >= riseM && nowM < setM;
    if (day) {
      return { start: 'hjemme', end: sunset ?? '–', startLabel: 'tænder', endLabel: 'slukker' };
    }
    return { start: sunset ?? '–', end: sunrise ?? '–', startLabel: 'slukkede', endLabel: 'tænder' };
  }

  function holdLabel(h: PowerHold): string {
    const at = new Date(h.until * 1000);
    const hm = at.toLocaleTimeString('da-DK', { hour: '2-digit', minute: '2-digit' });
    const sameDay = at.toDateString() === new Date().toDateString();
    return sameDay ? `til ${hm}` : `til i morgen ${hm}`;
  }

  const status = () => {
    if (expectAc != null) return 'skifter…';
    if (!store.fossibot.online) return 'offline';
    return hold ? holdLabel(hold) : '';
  };

  function setMode(next: PowerMode) {
    if (expectAc != null) return;
    if (next === 'on') expectAc = true;
    else if (next === 'off') expectAc = false;
    else settle();
    store.setPowerMode(next);
    if (next === 'auto') return;
    if (expectTimer) clearTimeout(expectTimer);
    expectTimer = setTimeout(settle, 25_000);
  }
</script>

<Card name="230 V" status={status()} online={acOn}>
  <div class="switchbot">
    <div
      class="power-window"
      class:muted={mode !== 'auto'}
      aria-label="{window.startLabel} {window.start}, {window.endLabel} {window.end}"
    >
      <div class="power-side">
        <span class="power-label">{window.startLabel}</span>
        <span class="power-sched">{window.start}</span>
      </div>
      <span class="power-dash">–</span>
      <div class="power-side">
        <span class="power-label">{window.endLabel}</span>
        <span class="power-sched">{window.end}</span>
      </div>
    </div>

    <div class="switchbot-glow" class:on={acOn} class:busy={expectAc != null} aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linecap="round">
        <path d="M12 3.2v8.2" />
        <path d="M7.2 6.4a8 8 0 1 0 9.6 0" />
      </svg>
    </div>

    <div class="power-modes" role="group" aria-label="230 volt-styring">
      <button type="button" class="action-btn" class:active={mode === 'on'} disabled={expectAc != null} onclick={() => setMode('on')}>tænd</button>
      <button type="button" class="action-btn" class:active={mode === 'auto'} disabled={expectAc != null} onclick={() => setMode('auto')}>auto</button>
      <button type="button" class="action-btn" class:active={mode === 'off'} disabled={expectAc != null} onclick={() => setMode('off')}>sluk</button>
    </div>
  </div>
</Card>

<style>
  .switchbot {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: space-evenly;
    height: 100%;
    width: 100%;
    gap: 14px;
    padding: 14px 6px;
  }

  .power-window {
    display: flex;
    align-items: flex-end;
    gap: 14px;
    transition: opacity 0.3s ease;
  }

  .power-window.muted {
    opacity: 0.35;
  }

  .power-side {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
  }

  .power-label {
    color: #7a7a7a;
    font-size: 0.62rem;
    font-weight: 300;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    line-height: 1;
  }

  .power-sched {
    color: #f2f2f2;
    font-size: 2.4rem;
    font-weight: 200;
    letter-spacing: 0.04em;
    font-variant-numeric: tabular-nums;
    line-height: 1;
  }

  .power-dash {
    color: #9b9b9b;
    font-size: 1.4rem;
    font-weight: 200;
    line-height: 1;
    padding-bottom: 0.2em;
  }

  .switchbot-glow {
    display: grid;
    place-items: center;
    width: min(28vw, 22vh, 132px);
    height: min(28vw, 22vh, 132px);
    padding: 0;
    border: 1px solid rgba(255, 255, 255, 0.22);
    border-radius: 50%;
    color: #555;
    transition: color 0.35s ease, border-color 0.35s ease, box-shadow 0.35s ease, opacity 0.25s ease;
  }

  .switchbot-glow svg {
    width: 42%;
    height: 42%;
  }

  .switchbot-glow.on {
    color: var(--accent);
    border-color: rgba(0, 128, 200, 0.55);
    box-shadow: 0 0 22px 2px rgba(0, 128, 200, 0.28);
  }

  .switchbot-glow.busy {
    opacity: 0.45;
  }

  .power-modes {
    display: flex;
    gap: 6px;
  }

  @media (max-width: 932px) {
    .switchbot {
      gap: 8px;
      padding: 6px 4px;
    }
    .power-window {
      gap: 10px;
    }
    .power-label {
      font-size: 0.52rem;
      letter-spacing: 0.12em;
    }
    .power-sched {
      font-size: 1.7rem;
    }
    .power-dash {
      font-size: 1rem;
    }
    .switchbot-glow {
      width: min(26vw, 20vh, 104px);
      height: min(26vw, 20vh, 104px);
    }
  }
</style>
