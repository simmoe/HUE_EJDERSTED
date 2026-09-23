<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import Card from '$lib/Card.svelte';
  import { airQualityBand, airQualityLabel, type AirBand, type AirStatus } from '$lib/air';

  const BANDS: AirBand[] = ['god', 'middel', 'dårlig'];

  let air = $state<AirStatus>({});
  let timer: ReturnType<typeof setInterval> | null = null;

  const reading = (value: number | null | undefined) =>
    value == null || Number.isNaN(value) ? '–' : String(Math.round(value));

  const band = $derived(air.online ? airQualityBand(air.pm25 ?? null) : '');

  const status = () => {
    if (!air.online) return 'offline';
    return airQualityLabel(air.pm25 ?? null);
  };

  async function refresh() {
    try {
      const res = await fetch('/api/air/status', { cache: 'no-store' });
      const data = (await res.json()) as AirStatus;
      air = data;
    } catch {
      air = { online: false };
    }
  }

  onMount(() => {
    void refresh();
    timer = setInterval(() => void refresh(), 30_000);
  });

  onDestroy(() => {
    if (timer) clearInterval(timer);
  });
</script>

<Card name="Luft" status={status()} online={!!air.online}>
  <div class="air">
    <div class="air-cols">
      <div class="air-col">
        <span class="air-label">pm2,5</span>
        <span class="air-reading">
          <span class="air-value">{reading(air.pm25)}</span>
          <span class="air-unit">µg</span>
        </span>
      </div>
      <div class="air-col">
        <span class="air-label">temp</span>
        <span class="air-reading">
          <span class="air-value">{reading(air.tempC)}</span>
          <span class="air-unit">°</span>
        </span>
      </div>
      <div class="air-col">
        <span class="air-label">fugt</span>
        <span class="air-reading">
          <span class="air-value">{reading(air.humidity)}</span>
          <span class="air-unit">%</span>
        </span>
      </div>
    </div>

    <div class="air-meter" class:off={!band} aria-hidden="true">
      <div class="air-pips">
        {#each BANDS as name}
          <span class="air-row">
            <svg class="air-arrow" class:on={band === name} viewBox="0 0 8 10" fill="none">
              <path d="M1.2 1.4 L6.4 5 L1.2 8.6" stroke="currentColor" stroke-width="1.15" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <span class="air-pip {name}" class:on={band === name}></span>
          </span>
        {/each}
      </div>
    </div>
  </div>
</Card>

<style>
  .air {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 100%;
    gap: 28px;
    padding: 0 8px 0 4px;
  }

  .air-cols {
    display: flex;
    align-items: center;
    justify-content: space-around;
    flex: 1;
    min-width: 0;
    gap: 12px;
  }

  .air-col {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }

  .air-label {
    color: #8a8a8a;
    font-size: 0.68rem;
    letter-spacing: 0.06em;
    text-transform: lowercase;
  }

  .air-reading {
    display: flex;
    align-items: baseline;
    gap: 4px;
  }

  .air-value {
    color: #f1f1f1;
    font-size: 2.4rem;
    font-weight: 100;
    line-height: 1;
  }

  .air-unit {
    color: #8a8a8a;
    font-size: 0.85rem;
  }

  .air-meter {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    opacity: 1;
    transition: opacity 0.4s ease;
  }

  .air-meter.off {
    opacity: 0.28;
  }

  .air-pips {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .air-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .air-arrow {
    width: 9px;
    height: 12px;
    color: rgba(255, 255, 255, 0.14);
    transition: color 0.45s ease, opacity 0.45s ease;
  }

  .air-arrow.on {
    color: #d8d4cc;
  }

  .air-pip {
    width: 11px;
    height: 11px;
    border-radius: 50%;
    opacity: 0.38;
    transition: opacity 0.45s ease, box-shadow 0.45s ease;
  }

  .air-pip.god {
    background: #8fa887;
  }

  .air-pip.middel {
    background: #c4a06a;
  }

  .air-pip.dårlig {
    background: #c07a72;
  }

  .air-pip.on {
    opacity: 1;
  }

  .air-pip.god.on {
    box-shadow: 0 0 10px rgba(143, 168, 135, 0.35);
  }

  .air-pip.middel.on {
    box-shadow: 0 0 10px rgba(196, 160, 106, 0.35);
  }

  .air-pip.dårlig.on {
    box-shadow: 0 0 10px rgba(192, 122, 114, 0.35);
  }
</style>
