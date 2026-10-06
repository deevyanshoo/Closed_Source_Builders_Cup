import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';

const gateway = process.env.GATEWAY_URL || 'http://127.0.0.1:8817';
const tokenFile = process.env.GATEWAY_DEV_TOKEN_FILE;

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5317,
    strictPort: true,
    proxy: {
      '/v2': {
        target: gateway,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyReq', proxyRequest => {
            proxyRequest.removeHeader('authorization');
            if (tokenFile) {
              try {
                const token = readFileSync(tokenFile, 'utf8').trim();
                if (token) proxyRequest.setHeader('Authorization', `Bearer ${token}`);
              } catch {
                // Without the private file the gateway responds as unauthenticated.
              }
            }
          });
        },
      },
    },
  },
});
