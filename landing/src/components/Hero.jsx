import { ChatEjemplo } from './ChatEjemplo'
import { Icono } from './Icono'

export function Hero() {
  return (
    <section id="inicio" className="wrap hero">
      <div className="hero__text">
        <p className="tag">Para distribuidoras de bebidas</p>
        <h1 className="hero__title">Tus clientes piden por WhatsApp. Trazr arma el pedido.</h1>
        <p className="hero__sub">
          Un bot que entiende cómo piden tus clientes, controla el stock real, confirma en minutos y
          te deja en la web solo lo que necesita tu decisión.
        </p>
        <div className="row">
          <a className="btn btn--primary" href="#probar"><Icono name="chat" size={18} />Probar el bot</a>
          <a className="btn btn--ghost" href="#como">Ver cómo funciona</a>
        </div>
        <p className="hero__login">¿Ya usás Trazr? <a href="/admin">Ingresá al panel</a></p>
      </div>
      <div className="hero__demo"><ChatEjemplo /></div>
    </section>
  )
}
