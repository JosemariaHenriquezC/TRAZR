import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// La landing se compila a app/web/landing y la sirve el servidor del bot (FastAPI) en «/».
// Los archivos quedan bajo /landing/… para no chocar con las rutas del panel (/admin).
export default defineConfig({
  plugins: [react()],
  base: '/landing/',
  build: { outDir: '../app/web/landing', emptyOutDir: true, assetsInlineLimit: 0 },
  server: {
    port: 5173,
    // Con «npm run dev», /admin y /privacidad van al servidor del bot (python -m app.server).
    proxy: { '/admin': 'http://127.0.0.1:8000', '/privacidad': 'http://127.0.0.1:8000' },
  },
})
