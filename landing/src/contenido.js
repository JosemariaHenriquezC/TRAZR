// Textos de la landing. Trazr se vende a muchas distribuidoras: nada de datos del negocio de
// prueba (catálogo, cantidades, horarios). «Hoy» es solo lo que el sistema ya hace.

export const SECCIONES = [
  { id: 'como', label: 'Cómo funciona' },
  { id: 'hoy', label: 'Qué hace hoy' },
  { id: 'panel', label: 'El panel' },
  { id: 'proximo', label: 'Próximamente' },
  { id: 'equipo', label: 'Equipo' },
]

export const PROBLEMA = [
  'Llega a cualquier hora y de cualquier forma: «lo de siempre», «2 cajas de branca», «un par de cocas grandes».',
  'Alguien lo copia al sistema, revisa si hay stock y calcula el total.',
  'El cliente espera entre 18 y 24 horas para saber si su pedido sale.',
]

export const PASOS = [
  {
    titulo: 'Pide como habla',
    texto: 'Entiende jerga, cajas y unidades sueltas, tamaños y «lo de siempre». Si algo no queda claro, pregunta solo eso.',
  },
  {
    titulo: 'Controla el stock',
    texto: 'Precios y stock salen de tu base, nunca de la IA. Si no alcanza, dice cuánto queda y ofrece ajustar.',
  },
  {
    titulo: 'Confirma o te consulta',
    texto: 'Lo que está dentro de los parámetros se confirma solo. El primer pedido de cada cliente lo aprobás vos.',
  },
  {
    titulo: 'Paga y recibe',
    texto: 'Efectivo al recibir o transferencia. El cliente sabe en qué turno de reparto sale, según tus horarios.',
  },
]

export const FUNCIONES = [
  { titulo: 'Lenguaje natural con IA', texto: 'Gemini interpreta el mensaje; si no responde, sigue con reglas propias.' },
  { titulo: 'Cajas y unidades sueltas', texto: '«25 con caja de 12» son 2 cajas y 1 suelta, al mismo precio por unidad.' },
  { titulo: 'Pregunta el tamaño', texto: 'Branca de 750 o de litro, Coca lata o 2,25: si no lo dijo, lo pregunta con botones.' },
  { titulo: 'Pago en el resumen', texto: 'Desde el segundo pedido trae el medio de siempre: confirmar es un toque.' },
  { titulo: '«Lo de siempre»', texto: 'Repite el último pedido con los precios de hoy.' },
  { titulo: 'Catálogo por categorías', texto: 'Tus categorías, agrupadas para el chat; «ver todo» manda la lista entera.' },
  { titulo: 'Dirección obligatoria', texto: 'Ningún pedido se registra sin dirección de envío ni sin confirmación del cliente.' },
  { titulo: 'Avisos al cliente', texto: 'Aprobado, rechazado o pago recibido: el bot avisa por el mismo canal.' },
]

export const PANEL = [
  'Los pedidos que esperan tu decisión, arriba de todo.',
  'Stock, precios y cajas de cada producto.',
  'Clientes con su dirección y sus pedidos.',
  'El pago se confirma con un clic y el bot le avisa al cliente.',
  'Se usa desde la compu o el celular.',
]

export const HOY = [
  'Pedidos por WhatsApp y Telegram en lenguaje natural',
  'Stock y precio reales en cada pedido',
  'Revisión del primer pedido de cada cliente',
  'Pagos en efectivo o transferencia, confirmados en la web',
  'Turnos de reparto con tus horarios y días',
]

export const PROXIMAMENTE = [
  'Límite de crédito por cliente',
  'Hablar con el cliente desde el panel cuando el bot no puede',
  'Aviso de recompra: cuándo suele pedir cada cliente',
  'Conexión con Tango Gestión',
]

export const POR_QUE = [
  { titulo: 'Frente a un bot genérico', texto: 'Responde, pero no conoce tu stock ni tus precios. Trazr arma el pedido con tu base.' },
  { titulo: 'Frente a las apps de las marcas', texto: 'Tu cliente no instala nada y la relación comercial no se terceriza.' },
  { titulo: 'Frente a sumar administrativos', texto: 'El costo no crece con cada pedido, y nadie atiende fuera de hora.' },
]

export const EQUIPO = ['Josemaria Henriquez Colacioppo', 'Nicolás Quinteros', 'Isa Pizzuto Dip']

export const TFI = 'Administración de Sistemas de Información · UTN Facultad Regional Tucumán · Comisión 4K1 · 2026'
