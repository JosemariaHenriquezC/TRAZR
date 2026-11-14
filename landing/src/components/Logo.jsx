import logo from '../assets/trazr-logo.svg'
import logoNegativo from '../assets/trazr-logo-negativo.svg'

// El logo cambia a su versión en negativo con el modo oscuro del sistema.
export function Logo({ height = 36 }) {
  return (
    <picture>
      <source srcSet={logoNegativo} media="(prefers-color-scheme: dark)" />
      <img src={logo} alt="trazr" style={{ height, width: 'auto', display: 'block' }} />
    </picture>
  )
}
