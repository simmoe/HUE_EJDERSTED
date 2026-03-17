<script lang="ts">
  import { untrack } from 'svelte';
  /**
   * VolumeKnob — cirkulær SVG-kontrolflade
   *
   * Buestart:  225° (7 på ur, nederst-venstre)
   * Bueslut:   495° = 135° (5 på ur, nederst-højre)
   * Sweep:     270°
   *
   * Koordinatsystem (clock-convention):
   *   0° = 12 o'clock (top), positiv = clockwise
   *   x = CX + R·sin(θ·π/180)
   *   y = CY − R·cos(θ·π/180)
   */

  let {
    value = 50,
    muted = false,
    disabled = false,
    onchange,
    onmute,
  }: {
    value?: number;
    muted?: boolean;
    disabled?: boolean;
    onchange?: (v: number) => void;
    onmute?: () => void;
  } = $props();

  // ── Geometri ──────────────────────────────────────────────────────────────
  const VB     = 280;
  const CX     = VB / 2;
  const CY     = VB / 2;
  const R      = 106;     // bueradius
  const SW     = 18;      // stroke-bredde
  const START  = 225;     // grader (clock)
  const SWEEP  = 270;     // samlet sweep

  const uid = crypto.randomUUID().slice(0, 8);

  function polar(deg: number) {
    const rad = (deg * Math.PI) / 180;
    return { x: CX + R * Math.sin(rad), y: CY - R * Math.cos(rad) };
  }

  function arc(from: number, to: number): string {
    const diff = to - from;
    if (Math.abs(diff) < 0.5) return '';
    const s = polar(from);
    const e = polar(to);
    const large = diff > 180 ? 1 : 0;
    return `M ${s.x.toFixed(2)} ${s.y.toFixed(2)} A ${R} ${R} 0 ${large} 1 ${e.x.toFixed(2)} ${e.y.toFixed(2)}`;
  }

  const trackPath = arc(START, START + SWEEP);

  // ── Reaktiv lokal state ───────────────────────────────────────────────────
  let local = $state(untrack(() => value));
  let dragging = $state(false);
  let didDrag = false;
  let el: SVGSVGElement;
  // Tap-detektion: pointer bevæget sig kun lidt → tap
  let downX = 0;
  let downY = 0;

  $effect(() => {
    if (!dragging) local = value;
  });

  const valueAngle = $derived(START + (local / 100) * SWEEP);
  const valuePath  = $derived(local <= 0 ? '' : arc(START, valueAngle));

  // Glasgradient radius (inderkreds)
  const innerR = R - SW / 2 - 6;

  // ── Pointer/touch drag ────────────────────────────────────────────────────
  function clockAngle(e: PointerEvent): number {
    const rect = el.getBoundingClientRect();
    const dx = e.clientX - (rect.left + rect.width / 2);
    const dy = e.clientY - (rect.top + rect.height / 2);
    let a = (Math.atan2(dx, -dy) * 180) / Math.PI;
    if (a < 0) a += 360;
    return a;
  }

  function angleToValue(angle: number): number {
    let shifted = angle - START;
    if (shifted < 0) shifted += 360;
    // Dead zone > SWEEP: snap til nærmeste ende
    if (shifted > SWEEP) {
      shifted = shifted - SWEEP < 360 - shifted ? SWEEP : 0;
    }
    return Math.round((shifted / SWEEP) * 100);
  }

  function applyPointer(e: PointerEvent) {
    const v = Math.max(0, Math.min(100, angleToValue(clockAngle(e))));
    local = v;
    onchange?.(v);
  }

  function onDown(e: PointerEvent) {
    e.preventDefault();
    downX = e.clientX;
    downY = e.clientY;
    didDrag = false;
    dragging = false;
    el.setPointerCapture(e.pointerId);
  }

  function onMove(e: PointerEvent) {
    if (!el.hasPointerCapture(e.pointerId)) return;
    if (disabled) return;
    const dx = e.clientX - downX;
    const dy = e.clientY - downY;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (!didDrag && dist < 15) return;
    didDrag = true;
    dragging = true;
    applyPointer(e);
  }

  function onUp(_e: PointerEvent) {
    if (!didDrag) {
      onmute?.();
    }
    dragging = false;
    didDrag = false;
  }
</script>

<div class="knob-root">
<svg
  bind:this={el}
  viewBox="0 0 {VB} {VB}"
  class="knob"
  class:disabled
  class:active={dragging}
  style="touch-action: none;"
  role="slider"
  tabindex="0"
  aria-valuenow={local}
  aria-valuemin={0}
  aria-valuemax={100}
  aria-disabled={disabled}
  onpointerdown={onDown}
  onpointermove={onMove}
  onpointerup={onUp}
>
  <defs>
    <!-- Liquid glass highlight -->
    <radialGradient id="g-{uid}" cx="38%" cy="28%" r="65%">
      <stop offset="0%"   stop-color="rgba(255,255,255,0.09)" />
      <stop offset="100%" stop-color="rgba(0,0,0,0)" />
    </radialGradient>
    <!-- Glow filter på value-arc -->
    <filter id="glow-{uid}" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="3" result="blur" />
      <feMerge>
        <feMergeNode in="blur" />
        <feMergeNode in="SourceGraphic" />
      </feMerge>
    </filter>
  </defs>

  <!-- Glas-baggrundscirkel -->
  <circle cx={CX} cy={CY} r={innerR} fill="url(#g-{uid})" />
  <circle cx={CX} cy={CY} r={innerR} fill="none"
    stroke="rgba(255,255,255,0.06)" stroke-width="1" />

  <!-- Track (grå) -->
  <path d={trackPath} fill="none"
    stroke="#595959" stroke-width={SW} stroke-linecap="round" />

  <!-- Value arc (blå/grå ved mute) -->
  {#if valuePath}
    <path d={valuePath} fill="none"
      stroke={muted ? '#595959' : '#0080c8'} stroke-width={SW} stroke-linecap="round"
      filter={dragging ? `url(#glow-${uid})` : undefined}
      class="value-arc" />
  {/if}

  <!-- Volumenværdi -->
  <text x={CX} y={CY + 10}
    text-anchor="middle" dominant-baseline="middle"
    class="number" class:muted>{local}</text>
</svg>
</div>

<style>
  .knob-root {
    position: relative;
    display: block;
  }
  .knob {
    width: 100%;
    height: auto;
    cursor: grab;
    -webkit-user-select: none;
    user-select: none;
    display: block;
  }
  .knob.disabled {
    cursor: default;
    opacity: 0.3;
  }
  .knob.active {
    cursor: grabbing;
  }
  .number {
    font-size: 80px;
    font-weight: 200;
    fill: #ebebeb;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display',
      'Helvetica Neue', system-ui, sans-serif;
    letter-spacing: -3px;
    pointer-events: none;
    transition: fill 0.2s;
  }
  .number.muted {
    fill: #595959;
  }
  .value-arc {
    transition: filter 0.15s ease;
  }
</style>
