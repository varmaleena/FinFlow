import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],server:{proxy:{'/v2':'http://127.0.0.1:8000','/v1':'http://127.0.0.1:8000'}},build:{rollupOptions:{output:{manualChunks:{charts:['recharts']}}}}});
