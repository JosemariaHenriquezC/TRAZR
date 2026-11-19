import { config } from '../config'
import { Icono } from './Icono'

// Los links salen del .env del servidor; un botón sin link no se muestra.
export function Probar() {
  const { whatsapp, telegram } = config
  return (
    <section id="probar" className="band band--monte" aria-labelledby="probar-t">
      <div className="wrap section probar">
        <div className="probar__text">
          <h2 className="h2 h2--big" id="probar-t">Probalo ahora: pedile algo al bot.</h2>
          <p>Es una demo: pedí lo que quieras, no se cobra ni se entrega nada.</p>
        </div>
        <div className="row">
          {whatsapp && (
            <a className="btn btn--limon" href={whatsapp} target="_blank" rel="noopener noreferrer">
              <Icono name="chat" size={18} />Pedir por WhatsApp
            </a>
          )}
          {telegram && (
            <a className="btn btn--outline-light" href={telegram} target="_blank" rel="noopener noreferrer">
              Abrir en Telegram
            </a>
          )}
          {!whatsapp && !telegram && (
            <p className="probar__off">El bot de prueba no está publicado en este momento.</p>
          )}
        </div>
      </div>
    </section>
  )
}
