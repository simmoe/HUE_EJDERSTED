import adapter from '@sveltejs/adapter-static';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	kit: {
		adapter: adapter({
			// hubctl static sets HUE_STATIC_OUT to a fresh directory. A manual
			// npm run build still fills backend/static, and hubctl never uploads that folder.
			pages: process.env.HUE_STATIC_OUT || '../backend/static',
			assets: process.env.HUE_STATIC_OUT || '../backend/static',
			fallback: 'index.html',
			precompress: false,
		}),
	},
	vitePlugin: {
		dynamicCompileOptions: ({ filename }) =>
			filename.includes('node_modules') ? undefined : { runes: true },
	},
};

export default config;
