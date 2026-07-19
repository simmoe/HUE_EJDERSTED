<script lang="ts">
  /**
   * Stemme → hub returnerer kun kø-metadata; afspilning styres på +page.
   */
  import { onDestroy } from 'svelte';
  import { showFeedback } from '$lib/feedback.svelte';

  let {
    onvoice,
  }: {
    onvoice?: (data: Record<string, unknown>) => { handled: boolean; message?: string; error?: string } | void;
  } = $props();

  let listening = $state(false);
  let textQuery = $state('');
  let textSearching = $state(false);
  let voiceLanguage = $state<'da-DK' | 'en-US'>('en-US');
  let pressActive = false;
  let recordingStream: MediaStream | null = null;
  let recorder: MediaRecorder | null = null;
  let recordingTimer: ReturnType<typeof setTimeout> | null = null;

  const VOICE_TIMEOUT_MS = 12_000;
  const RECORDING_MS = 5_000;

  async function handleResult(transcript: string) {
    listening = false;
    textSearching = true;
    showFeedback(transcript, { duration: 8000 });
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), VOICE_TIMEOUT_MS);
    try {
      showFeedback(`søger: ${transcript}`, { duration: 12_000 });
      const r = await fetch('/api/spotify/voice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcript }),
        signal: ctrl.signal,
      });
      if (!r.ok) {
        let detail = `hub-fejl ${r.status}`;
        try {
          const err = await r.json();
          if (err?.error || err?.detail) detail = String(err.error ?? err.detail);
        } catch {
          /* keep HTTP fallback */
        }
        showFeedback(detail, { kind: 'error', duration: 7000 });
        return;
      }
      const data = (await r.json()) as Record<string, unknown>;
      const handled = onvoice?.(data);
      if (data.ok === false && data.error) {
        showFeedback(String(data.error), { kind: 'error', duration: 7000 });
      } else if (data.action === 'pause') {
        showFeedback(data.ok ? 'pause' : 'pause fejlede', { kind: data.ok ? 'info' : 'error' });
      } else if (data.action === 'local_nav') {
        if (handled?.error) showFeedback(handled.error, { kind: 'error' });
      } else if (data.action === 'use_play_button') {
        showFeedback('Tryk play');
      } else if (data.action === 'enqueue' || data.action === 'enqueue_queue') {
        showFeedback(handled?.message || 'Tilføjet til kø', { kind: 'success' });
      } else if (data.name && typeof data.name === 'string') {
        showFeedback(data.name);
      } else if (!data.ok && data.action === 'search') {
        showFeedback('ikke fundet', { kind: 'error', duration: 7000 });
      } else if (handled?.error) {
        showFeedback(handled.error, { kind: 'error', duration: 7000 });
      } else if (!handled?.handled) {
        showFeedback('kunne ikke bruge svaret', { kind: 'error', duration: 7000 });
      }
    } catch (e) {
      showFeedback((e as Error)?.name === 'AbortError' ? 'hub svarer ikke' : 'ingen forbindelse', { kind: 'error', duration: 7000 });
    } finally {
      clearTimeout(timeout);
      textSearching = false;
    }
  }

  function submitTextSearch() {
    const q = textQuery.trim();
    if (!q || textSearching) return;
    textQuery = '';
    void handleResult(q);
  }

  function setVoiceCaptureActive(active: boolean) {
    window.dispatchEvent(new CustomEvent('hue:voice-capture', { detail: { active } }));
  }

  async function startListening() {
    if (listening) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setVoiceCaptureActive(false);
      showFeedback('lydoptagelse ikke understøttet', { kind: 'error' });
      return;
    }
    try {
      recordingStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
      const preferredType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']
        .find((type) => MediaRecorder.isTypeSupported(type));
      const chunks: Blob[] = [];
      recorder = preferredType
        ? new MediaRecorder(recordingStream, { mimeType: preferredType })
        : new MediaRecorder(recordingStream);
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      };
      recorder.onerror = () => {
        listening = false;
        showFeedback('kunne ikke optage lyd', { kind: 'error' });
      };
      recorder.onstop = async () => {
        if (recordingTimer) clearTimeout(recordingTimer);
        recordingTimer = null;
        recordingStream?.getTracks().forEach((track) => track.stop());
        recordingStream = null;
        const mimeType = recorder?.mimeType || preferredType || 'audio/webm';
        recorder = null;
        listening = false;
        setVoiceCaptureActive(false);
        const audio = new Blob(chunks, { type: mimeType });
        if (!audio.size) {
          showFeedback('ingen lyd optaget', { kind: 'error' });
          return;
        }
        textSearching = true;
        showFeedback('fortolker tale...', { duration: 12_000 });
        try {
          const response = await fetch('/api/spotify/voice/transcribe', {
            method: 'POST',
            headers: {
              'Content-Type': mimeType,
              'X-Voice-Language': voiceLanguage,
            },
            body: audio,
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok || !data?.transcript) {
            showFeedback(String(data?.error || 'talegenkendelse fejlede'), { kind: 'error', duration: 7000 });
            return;
          }
          await handleResult(String(data.transcript));
        } catch {
          showFeedback('ingen forbindelse til talegenkendelse', { kind: 'error', duration: 7000 });
        } finally {
          textSearching = false;
        }
      };
      listening = true;
      recorder.start();
      recordingTimer = setTimeout(() => {
        if (recorder?.state === 'recording') recorder.stop();
      }, RECORDING_MS);
    } catch (error) {
      recordingStream?.getTracks().forEach((track) => track.stop());
      recordingStream = null;
      recorder = null;
      listening = false;
      setVoiceCaptureActive(false);
      showFeedback((error as Error)?.name === 'NotAllowedError' ? 'mikrofon ikke tilladt' : 'kunne ikke åbne mikrofonen', { kind: 'error' });
    }
  }

  function onPressStart(e: PointerEvent) {
    if (listening) return;
    e.preventDefault();
    pressActive = true;
    // The garden kiosk continuously owns a camera MediaStream. Release it on
    // touch-down before touch-up opens the microphone MediaStream.
    setVoiceCaptureActive(true);
  }

  function onPressEnd(e: PointerEvent) {
    if (!pressActive) return;
    e.preventDefault();
    pressActive = false;
    void startListening();
  }

  function onPressCancel() {
    pressActive = false;
    setVoiceCaptureActive(false);
  }

  onDestroy(() => {
    if (recordingTimer) clearTimeout(recordingTimer);
    if (recorder?.state === 'recording') recorder.stop();
    recordingStream?.getTracks().forEach((track) => track.stop());
    setVoiceCaptureActive(false);
  });
</script>

<div class="center-area">
  <button
    type="button"
    class="voice-btn"
    class:listening
    onpointerdown={onPressStart}
    onpointerup={onPressEnd}
    onpointercancel={onPressCancel}
    aria-label="Tryk og tal"
    title="Tryk og tal"
  >
    <span class="voice-ring"></span>
    <svg class="mic-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0" />
      <line x1="12" y1="17" x2="12" y2="21" />
    </svg>
    {#if listening}
      <span class="lang-badge">{voiceLanguage === 'da-DK' ? 'DA' : 'EN'}</span>
    {/if}
  </button>

  <div class="language-toggle" role="group" aria-label="Sprog for talegenkendelse">
    <button
      type="button"
      class:active={voiceLanguage === 'da-DK'}
      aria-pressed={voiceLanguage === 'da-DK'}
      disabled={listening}
      onclick={() => (voiceLanguage = 'da-DK')}
    >
      DA
    </button>
    <button
      type="button"
      class:active={voiceLanguage === 'en-US'}
      aria-pressed={voiceLanguage === 'en-US'}
      disabled={listening}
      onclick={() => (voiceLanguage = 'en-US')}
    >
      EN
    </button>
  </div>

  {#if listening}
    <span class="feedback listening-text">lytter</span>
  {/if}

  <form class="desktop-search" onsubmit={(e) => { e.preventDefault(); submitTextSearch(); }}>
    <input
      bind:value={textQuery}
      type="search"
      autocomplete="off"
      autocapitalize="none"
      spellcheck="false"
      placeholder="søg musik"
      aria-label="Søg musik"
      disabled={textSearching}
    />
    <button type="submit" disabled={textSearching || !textQuery.trim()}>
      {textSearching ? 'søger' : 'søg'}
    </button>
  </form>
</div>

<style>
  .center-area {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 28px;
  }

  .voice-btn {
    position: relative;
    flex: 0 0 96px;
    width: 96px;
    height: 96px;
    aspect-ratio: 1;
    border-radius: 50%;
    border: 2px solid rgba(255, 255, 255, 0.18);
    background: rgba(255, 255, 255, 0.025);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    -webkit-tap-highlight-color: transparent;
    transition: border-color 0.4s ease;
    overflow: hidden;
    color: #9b9b9b;
  }

  .voice-btn:active {
    border-color: rgba(0, 128, 200, 0.4);
  }

  .voice-btn.listening {
    border-color: rgba(0, 150, 225, 0.72);
    color: #0080c8;
  }

  .lang-badge {
    position: absolute;
    bottom: 12px;
    font-size: 0.62rem;
    font-weight: 400;
    letter-spacing: 0.12em;
    color: #d8c87a;
    pointer-events: none;
  }

  .language-toggle {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    width: 84px;
    margin-top: -16px;
    padding: 2px;
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.035);
  }

  .language-toggle button {
    min-height: 26px;
    padding: 0 8px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: #9b9b9b;
    cursor: pointer;
    font: inherit;
    font-size: 0.7rem;
    letter-spacing: 0.1em;
  }

  .language-toggle button.active {
    background: rgba(0, 128, 200, 0.22);
    color: #f2f2f2;
  }

  .language-toggle button:disabled {
    cursor: default;
    opacity: 0.55;
  }

  .mic-icon {
    width: 24px;
    height: 24px;
    transition: color 0.3s;
  }

  .voice-ring {
    position: absolute;
    inset: -1px;
    border-radius: 50%;
    border: 2px solid transparent;
    transition: border-color 0.3s ease;
  }

  .voice-btn.listening .voice-ring {
    border-color: rgba(0, 150, 225, 0.85);
    animation: ring-pulse 1.5s ease-in-out infinite;
  }

  @keyframes ring-pulse {
    0%, 100% { opacity: 0.3; transform: scale(1); }
    50%      { opacity: 1;   transform: scale(1.04); }
  }

  .feedback {
    font-size: 0.75rem;
    font-weight: 300;
    letter-spacing: 0.06em;
    color: #f2f2f2;
    text-align: center;
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .listening-text {
    color: #0080c8;
  }

  .desktop-search {
    display: none;
  }

  @media (hover: hover) and (pointer: fine) {
    .desktop-search {
      display: flex;
      width: min(320px, 100%);
      gap: 8px;
      align-items: center;
    }

    .desktop-search input {
      flex: 1;
      min-width: 0;
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.02);
      color: #d8d8d8;
      font: inherit;
      font-size: 0.8rem;
      font-weight: 300;
      letter-spacing: 0.04em;
      padding: 10px 14px;
      outline: none;
    }

    .desktop-search input:focus {
      border-color: rgba(0, 128, 200, 0.45);
    }

    .desktop-search input::placeholder {
      color: #9b9b9b;
    }

    .desktop-search button {
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 999px;
      background: none;
      color: #c2c2c2;
      cursor: pointer;
      font: inherit;
      font-size: 0.72rem;
      font-weight: 300;
      letter-spacing: 0.12em;
      padding: 10px 13px;
      text-transform: uppercase;
    }

    .desktop-search button:disabled {
      cursor: default;
      opacity: 0.35;
    }
  }
</style>
