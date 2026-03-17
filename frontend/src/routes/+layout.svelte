<script lang="ts">
	import '../app.css';
	let { children } = $props();
</script>

<svelte:head>
	<title>HUE · EJDERSTED</title>
</svelte:head>

{@render children()}
