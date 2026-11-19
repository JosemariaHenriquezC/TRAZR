import simbolo from '../assets/trazr-simbolo.svg'

// Una conversación de ejemplo, tal como la contesta el bot: cajas, pregunta del tamaño y
// resumen con pago y turno de reparto. Es una ilustración, no datos de un negocio.
export function ChatEjemplo() {
  return (
    <figure className="chat" aria-label="Ejemplo de conversación con el bot">
      <div className="chat__head">
        <img src={simbolo} alt="" className="chat__avatar" />
        <div>
          <div className="chat__name">Distribuidora de ejemplo</div>
          <div className="chat__status">bot de pedidos · responde al instante</div>
        </div>
      </div>
      <div className="chat__body">
        <p className="bubble bubble--out">hola! mandame 2 cajas de branca y 24 norte</p>
        <div className="bubble bubble--in">
          Anoté las <b>24 Norte</b>. ¿De qué tamaño la Branca? Tengo 1L, 750ml, 450ml o Miniatura.
          <div className="chat__chips" aria-hidden="true">
            <span className="chip">1L</span>
            <span className="chip chip--on">750ml</span>
            <span className="chip">450ml</span>
          </div>
        </div>
        <div className="bubble bubble--in bubble--wide">
          <div className="resumen__title">Revisá tu pedido</div>
          <div className="resumen__row"><span>24 × Fernet Branca 750ml <span className="muted">(2 cajas)</span></span><span className="num">$ 391.608</span></div>
          <div className="resumen__row"><span>24 × Norte lata 473ml <span className="muted">(1 bulto)</span></span><span className="num">$ 36.000</span></div>
          <div className="resumen__row resumen__total"><span>Total · envío incluido</span><span className="num">$ 427.608</span></div>
          <div className="muted resumen__meta">Pago: transferencia, como la última vez<br />Sale hoy en el turno tarde</div>
          <div className="chat__chips" aria-hidden="true">
            <span className="chip chip--on">Confirmar</span>
            <span className="chip chip--plain">Cambiar pago</span>
            <span className="chip chip--plain">Cancelar</span>
          </div>
        </div>
      </div>
    </figure>
  )
}
