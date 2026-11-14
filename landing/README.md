# Landing de Trazr

Página de inicio de Trazr (para distribuidoras), hecha en React + Vite con el sistema de
diseño de Trazr. La sirve el mismo servidor del bot en `/`: no hay que levantar nada aparte.

- **Ya compilada:** la versión lista está en `app/web/landing/` (se sube al repo). Para usarla
  alcanza con `python -m app.server`; no hace falta Node.
- **Links de «Probar el bot»:** salen del `.env` (`LANDING_WHATSAPP`, `LANDING_TELEGRAM`). El
  servidor los pone al servir la página, así que cambiarlos no requiere recompilar. Sin
  `LANDING_TELEGRAM` usa el usuario del bot de Telegram que está corriendo.
- **Textos:** en `src/contenido.js`. Nada de datos del negocio de prueba (Trazr se vende a
  muchas distribuidoras) y «Próximamente» separado de lo que ya funciona.

## Cambiar algo (necesita Node 18+)

```bash
cd landing
npm install        # la primera vez
npm run dev        # http://localhost:5173 (con el bot corriendo, «Ingresar» va al panel)
npm run build      # recompila a app/web/landing — subí también esos archivos
```

La captura del panel (`src/assets/panel.png`) es de datos de demostración.
