<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import Card from '$lib/Card.svelte';
  import { airQualityLabel, type AirStatus } from '$lib/air';

  let air = $state<AirStatus>({});
  let timer: ReturnType<typeof setInterval> | null = null;

  const reading = (value: number | null | undefined) =>
    value == null || Number.isNaN(value) ? '–' : String(Math.round(value));

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
</Card>

<style>
  .air {
    display: flex;
    align-items: center;
    justify-content: space-around;
    height: 100%;
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
</style>
