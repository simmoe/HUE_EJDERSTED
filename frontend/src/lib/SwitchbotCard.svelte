<script lang="ts">
  import Card from '$lib/Card.svelte';
  import { store, type PowerMode } from '$lib/ws.svelte';

  let expectAc = $state<boolean | null>(null);
  let expectTimer: ReturnType<typeof setTimeout> | null = null;

  const acOn = $derived(expectAc ?? !!store.fossibot.acOn);
  const mode = $derived(store.power.mode === 'on' || store.power.mode === 'off' ? store.power.mode : '');

  $effect(() => {
    const live = !!store.fossibot.acOn;
    if (expectAc != null && live === expectAc) settle();
  });

  function settle() {
    expectAc = null;
    if (expectTimer) clearTimeout(expectTimer);
    expectTimer = null;
  }

  const status = () => {
    if (expectAc != null) return 'skifter…';
    if (!store.fossibot.online) return 'offline';
    return '';
  };

  function setMode(next: PowerMode) {
    if (expectAc != null) return;
    expectAc = next === 'on';
    store.setPowerMode(next);
    if (expectTimer) clearTimeout(expectTimer);
    expectTimer = setTimeout(settle, 25_000);
  }
</script>

<Card name="230 V" status={status()} online={acOn}>
  <div class="switchbot">
    <div class="switchbot-glow" class:on={acOn} class:busy={expectAc != null} aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linecap="round">
        <path d="M12 3.2v8.2" />
        <path d="M7.2 6.4a8 8 0 1 0 9.6 0" />
      </svg>
    </div>

    <div class="power-modes" role="group" aria-label="230 volt-styring">
      <button type="button" class="action-btn" class:active={mode === 'on'} disabled={expectAc != null} onclick={() => setMode('on')}>tænd</button>
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
    .switchbot-glow {
      width: min(26vw, 20vh, 104px);
      height: min(26vw, 20vh, 104px);
    }
  }
</style>
