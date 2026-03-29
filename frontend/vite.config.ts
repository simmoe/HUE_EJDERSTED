import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		proxy: {
			'/api': { target: 'https://localhost:8443', secure: false },
			'/ws':  { target: 'wss://localhost:8443', ws: true, secure: false },
		},
	},
});
