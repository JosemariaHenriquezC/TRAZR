import { useEffect, useState } from 'react'
import { SECCIONES } from '../contenido'
import { Icono } from './Icono'
import { Logo } from './Logo'

export function Nav() {
  const [open, setOpen] = useState(false)

  // Con el menú abierto, Escape lo cierra.
  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const close = () => setOpen(false)

  return (
    <header className="nav">
      <nav className="wrap nav__bar" aria-label="Principal">
        <a href="#inicio" className="nav__logo" aria-label="Trazr, inicio"><Logo /></a>
        <div className="nav__links">
          {SECCIONES.map((s) => <a key={s.id} className="navlink" href={`#${s.id}`}>{s.label}</a>)}
        </div>
        <div className="nav__actions">
          <a className="btn btn--ghost btn--sm" href="/admin">Ingresar</a>
          <a className="btn btn--primary btn--sm" href="#probar">Probar el bot</a>
        </div>
        <button type="button" className="nav__toggle" aria-expanded={open} aria-controls="menu-movil"
          aria-label={open ? 'Cerrar menú' : 'Abrir menú'} onClick={() => setOpen(!open)}>
          <Icono name={open ? 'cerrar' : 'menu'} />
        </button>
      </nav>
      {open && (
        <div id="menu-movil" className="nav__menu wrap">
          {SECCIONES.map((s) => <a key={s.id} className="navlink" href={`#${s.id}`} onClick={close}>{s.label}</a>)}
          <div className="nav__menu-actions">
            <a className="btn btn--primary" href="#probar" onClick={close}>Probar el bot</a>
            <a className="btn btn--ghost" href="/admin">Ingresar</a>
          </div>
        </div>
      )}
    </header>
  )
}
