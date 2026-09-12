<script lang="ts">
  import { resolveCover } from '$lib/coverArt';

  let {
    title = '',
    artist = '',
    src: given = '',
  }: {
    title?: string;
    artist?: string;
    src?: string;
  } = $props();

  let src = $state(given);
  let failed = $state(false);

  $effect(() => {
    if (given) {
      src = given;
      failed = false;
      return;
    }
    let cancelled = false;
    src = '';
    failed = false;
    void resolveCover(artist, title).then((url) => {
      if (cancelled) return;
      if (url) src = url;
      else failed = true;
    });
    return () => {
      cancelled = true;
    };
  });
</script>

{#if src && !failed}
  <img
    class="podcast-cover"
    {src}
    alt=""
    loading="lazy"
    onerror={() => {
      failed = true;
    }}
  />
{:else}
  <div class="podcast-cover playlist-text-cover" aria-hidden="true">
    <span class="playlist-cover-title">{title}</span>
    {#if artist}
      <span class="playlist-cover-artist">{artist}</span>
    {/if}
  </div>
{/if}

<style>
  .podcast-cover {
    flex: 0 0 88px;
    width: 88px;
    height: 88px;
    border-radius: 8px;
    object-fit: cover;
    background: rgba(255, 255, 255, 0.04);
  }
  .playlist-text-cover {
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    gap: 4px;
    padding: 9px;
    background:
      radial-gradient(circle at 22% 18%, rgba(255, 255, 255, 0.1), transparent 34%),
      linear-gradient(145deg, #242424, #111);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);
  }
  .playlist-cover-title,
  .playlist-cover-artist {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    overflow: hidden;
    line-height: 1.05;
    text-transform: uppercase;
    word-break: break-word;
  }
  .playlist-cover-title {
    color: #f1f1f1;
    font-size: 0.64rem;
    font-weight: 500;
    letter-spacing: 0.08em;
    -webkit-line-clamp: 3;
  }
  .playlist-cover-artist {
    color: #8a8a8a;
    font-size: 0.5rem;
    letter-spacing: 0.12em;
    -webkit-line-clamp: 2;
  }
</style>
