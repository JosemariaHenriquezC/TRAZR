// Links de «Probar el bot». Los pone el servidor al servir la página (LANDING_WHATSAPP y
// LANDING_TELEGRAM en el .env): así cambian sin recompilar. Con «npm run dev» no hay
// servidor que los ponga, y los botones no se muestran.
function readConfig() {
  try {
    const raw = document.getElementById('trazr-config')?.textContent ?? ''
    const data = JSON.parse(raw)
    return data && typeof data === 'object' ? data : {}
  } catch {
    return {}
  }
}

export const config = readConfig()
