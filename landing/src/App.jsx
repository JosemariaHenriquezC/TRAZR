import { Footer } from './components/Footer'
import { Hero } from './components/Hero'
import { Nav } from './components/Nav'
import { Probar } from './components/Probar'
import { ComoFunciona, Equipo, Panel, PorQue, Problema, Proximamente, QueHaceHoy } from './components/Secciones'

export default function App() {
  return (
    <>
      <a className="skip" href="#contenido">Saltar al contenido</a>
      <Nav />
      <main id="contenido">
        <Hero />
        <Problema />
        <ComoFunciona />
        <QueHaceHoy />
        <Panel />
        <Proximamente />
        <PorQue />
        <Probar />
        <Equipo />
      </main>
      <Footer />
    </>
  )
}
