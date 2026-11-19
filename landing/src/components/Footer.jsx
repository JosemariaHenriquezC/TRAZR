import { Logo } from './Logo'

export function Footer() {
  return (
    <footer className="footer">
      <div className="wrap footer__bar">
        <Logo height={28} />
        <div className="footer__links">
          <a className="navlink" href="/privacidad">Privacidad</a>
          <a className="navlink" href="/admin">Ingresar al panel</a>
          <span className="muted">© 2026 Trazr · Tucumán</span>
        </div>
      </div>
    </footer>
  )
}
