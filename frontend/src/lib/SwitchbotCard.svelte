<script lang="ts">
  import Card from '$lib/Card.svelte';
  import { store, type HoldDuration, type PowerHold } from '$lib/ws.svelte';

  // Auto is the ground state (45 % floor, 55 % resume). A tap is a hold with an
  // expiry, chosen on a small wheel. "auto" drops the hold again.
  const WHEEL: { id: HoldDuration; label: string }[] = [
    { id: '1h', label: '1 t' },
    { id: '2h', label: '2 t' },
    { id: '5h', label: '5 t' },
    { id: 'tomorrow', label: 'i morgen' }
  ];

  let picking = $state(false);
  let expectAc = $state<boolean | null>(null);
  let expectTimer: ReturnType<typeof setTimeout> | null = null;

  const acOn = $derived(expectAc ?? !!store.fossibot.acOn);
  const hold = $derived(store.power.hold);

  $effect(() => {
    const live = !!store.fossibot.acOn;
    if (expectAc != null && live === expectAc) settle();
  });

  function settle() {
    expectAc = null;
    if (expectTimer) clearTimeout(expectTimer);
    expectTimer = null;
  }

  function holdLabel(h: PowerHold): string {
    const at = new Date(h.until * 1000);
    const hm = at.toLocaleTimeString('da-DK', { hour: '2-digit', minute: '2-digit' });
    const sameDay = at.toDateString() === new Date().toDateString();
    return sameDay ? `til ${hm}` : `til i morgen ${hm}`;
  }

  // The glowing button already says on/off; the header only adds what it cannot.
  const status = () => {
    if (expectAc != null) return 'skifter…';
    if (picking) return acOn ? 'sluk i…' : 'tænd i…';
    if (!store.fossibot.online) return 'offline';
    return hold ? holdLabel(hold) : '';
  };

  function tap() {
    if (expectAc != null) return;
    picking = !picking;
  }

  function choose(duration: HoldDuration) {
    const target = !acOn;
    picking = false;
    expectAc = target;
    store.setPowerHold(target, duration);
    // The finger may miss or the bot may be off; do not show "skifter…" forever.
    if (expectTimer) clearTimeout(expectTimer);
    expectTimer = setTimeout(settle, 25_000);
  }

  function backToAuto() {
    picking = false;
    store.clearPowerHold();
  }
</script>

<Card name="230 V" status={status()} online={acOn}>
  <div class="switchbot">
    <button
      type="button"
      class="switchbot-btn"
      class:on={acOn}
      class:busy={expectAc != null}
      class:picking
      disabled={expectAc != null}
      onclick={tap}
      aria-label={acOn ? '230 volt tændt, tryk for at slukke' : '230 volt slukket, tryk for at tænde'}
      aria-expanded={picking}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linecap="round" aria-hidden="true">
        <path d="M12 3.2v8.2" />
        <path d="M7.2 6.4a8 8 0 1 0 9.6 0" />
      </svg>
    </button>

    {#if picking}
      <div class="switchbot-wheel" role="group" aria-label={acOn ? 'Sluk 230 volt i' : 'Tænd 230 volt i'}>
        {#each WHEEL as step (step.id)}
          <button type="button" class="action-btn" onclick={() => choose(step.id)}>{step.label}</button>
        {/each}
      </div>
    {:else if hold}
      <div class="switchbot-wheel" role="group" aria-label="230 volt-styring">
        <button type="button" class="action-btn" onclick={backToAuto}>auto</button>
      </div>
    {/if}
  </div>
</Card>

<style>
  .switchbot {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    width: 100%;
    gap: 18px;
  }

  .switchbot-btn {
    display: grid;
    place-items: center;
    width: min(28vw, 22vh, 132px);
    height: min(28vw, 22vh, 132px);
    padding: 0;
    border: 1px solid rgba(255, 255, 255, 0.22);
    border-radius: 50%;
    background: transparent;
    color: #555;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: color 0.35s ease, border-color 0.35s ease, box-shadow 0.35s ease, opacity 0.25s ease;
  }

  .switchbot-btn svg {
    width: 42%;
    height: 42%;
  }

  .switchbot-btn.on {
    color: var(--accent);
    border-color: rgba(0, 128, 200, 0.55);
    box-shadow: 0 0 22px 2px rgba(0, 128, 200, 0.28);
  }

  .switchbot-btn.picking {
    border-color: rgba(255, 255, 255, 0.6);
  }

  .switchbot-btn:active:not(:disabled) {
    opacity: 0.7;
  }

  .switchbot-btn.busy {
    opacity: 0.45;
  }

  .switchbot-btn:disabled {
    cursor: default;
  }

  .switchbot-wheel {
    display: flex;
    gap: 6px;
  }

  @media (max-width: 932px) {
    .switchbot-btn {
      width: min(26vw, 20vh, 104px);
      height: min(26vw, 20vh, 104px);
    }
  }
</style>
