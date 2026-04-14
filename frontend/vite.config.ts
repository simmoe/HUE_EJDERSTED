import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

/** Mac `npm run dev`: standard mod Pi (samme hub som kiosk). Overstyr: VITE_HUB_ORIGIN=https://localhost:8443 */
const HUB = process.env.VITE_HUB_ORIGIN ?? 'https://192.168.86.16:8443';
const HUB_WS = HUB.replace(/^https:/i, 'wss:');

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		proxy: {
			'/api': { target: HUB, changeOrigin: true, secure: false },
			'/ws': { target: HUB_WS, ws: true, secure: false },
		},
	},
});
