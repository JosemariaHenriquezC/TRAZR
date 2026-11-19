import panelImg from '../assets/panel.png'
import { EQUIPO, FUNCIONES, HOY, PANEL, PASOS, POR_QUE, PROBLEMA, PROXIMAMENTE, TFI } from '../contenido'
import { Icono } from './Icono'

function Head({ eyebrow, title, id }) {
  return (
    <>
      <p className="eyebrow">{eyebrow}</p>
      <h2 className="h2" id={id}>{title}</h2>
    </>
  )
}

const pad = (n) => String(n).padStart(2, '0')

export function Problema() {
  return (
    <section className="wrap section split" aria-labelledby="problema-t">
      <div className="split__head"><Head eyebrow="El problema" id="problema-t" title="El pedido por WhatsApp hoy se transcribe a mano." /></div>
      <ol className="lines">
        {PROBLEMA.map((t, i) => (
          <li key={t}><span className="mono">{pad(i + 1)}</span><p>{t}</p></li>
        ))}
      </ol>
    </section>
  )
}

export function ComoFunciona() {
  return (
    <section id="como" className="wrap section" aria-labelledby="como-t">
      <Head eyebrow="Cómo funciona" id="como-t" title="Del mensaje al reparto, sin que nadie copie nada." />
      <div className="grid grid--4">
        {PASOS.map((p, i) => (
          <article key={p.titulo} className="card">
            <span className="mono accent">{pad(i + 1)}</span>
            <h3 className="h3">{p.titulo}</h3>
            <p className="muted">{p.texto}</p>
          </article>
        ))}
      </div>
    </section>
  )
}

export function QueHaceHoy() {
  return (
    <section id="hoy" className="band band--raised" aria-labelledby="hoy-t">
      <div className="wrap section">
        <Head eyebrow="Qué hace hoy" id="hoy-t" title="Funcionando, no en una presentación." />
        <ul className="features">
          {FUNCIONES.map((f) => (
            <li key={f.titulo}>
              <span className="ok"><Icono name="check" size={22} strokeWidth={2.2} /></span>
              <div><strong>{f.titulo}</strong><p className="muted">{f.texto}</p></div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}

export function Panel() {
  return (
    <section id="panel" className="wrap section split split--center" aria-labelledby="panel-t">
      <div className="split__head">
        <Head eyebrow="El panel de la distribuidora" id="panel-t" title="El chat es para tus clientes. La web, para vos." />
        <ul className="bullets">{PANEL.map((t) => <li key={t}>{t}</li>)}</ul>
        <a className="btn btn--ghost" href="/admin">Ingresar al panel</a>
      </div>
      <div className="panel-shot">
        <img src={panelImg} width="1440" height="1000" loading="lazy"
          alt="Tablero del panel de Trazr: pedidos que esperan decisión, por cobrar y productos por acabarse" />
      </div>
    </section>
  )
}

export function Proximamente() {
  return (
    <section id="proximo" className="band band--sunken" aria-labelledby="proximo-t">
      <div className="wrap section">
        <Head eyebrow="Hacia dónde vamos" id="proximo-t" title="Lo que ya está y lo que viene." />
        <div className="row row--cards">
          <div className="card card--big">
            <span className="badge badge--ok"><span className="dot" />Hoy</span>
            <ul className="bullets bullets--ink">{HOY.map((t) => <li key={t}>{t}</li>)}</ul>
          </div>
          <div className="card card--big card--dashed">
            <span className="badge badge--warn"><span className="dot" />Próximamente</span>
            <ul className="bullets bullets--ink">{PROXIMAMENTE.map((t) => <li key={t}>{t}</li>)}</ul>
          </div>
        </div>
      </div>
    </section>
  )
}

export function PorQue() {
  return (
    <section className="wrap section" aria-labelledby="porque-t">
      <Head eyebrow="Por qué Trazr" id="porque-t" title="El canal que tus clientes ya usan. El dato, tuyo." />
      <div className="grid grid--3">
        {POR_QUE.map((p) => (
          <div key={p.titulo} className="topline"><h3 className="h3">{p.titulo}</h3><p className="muted">{p.texto}</p></div>
        ))}
      </div>
    </section>
  )
}

export function Equipo() {
  return (
    <section id="equipo" className="wrap section split" aria-labelledby="equipo-t">
      <div className="split__head">
        <p className="eyebrow">Equipo</p>
        <h2 className="h2 h2--sm" id="equipo-t">Trabajo Integrador Final</h2>
        <p className="muted">{TFI}</p>
      </div>
      <ul className="names">{EQUIPO.map((n) => <li key={n}>{n}</li>)}</ul>
    </section>
  )
}
