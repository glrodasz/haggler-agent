/**
 * Every piece of visible copy lives here (Spanish UI). Pages and components never hard-code
 * text, so wording can be reviewed in one place and a second locale stays possible.
 */

export const es = {
  site: {
    name: "haggler",
    tagline: "Precios de suscripciones por país, con su riesgo",
    description: (residence: string) =>
      `Comparador de precios oficiales de suscripciones (Netflix, Spotify, iCloud+, YouTube Premium…) en distintos países, con el riesgo de cada opción para alguien que vive en ${residence}.`,
    skipToContent: "Saltar al contenido",
    navLabel: "Principal",
    nav: {
      home: "Comparador",
      services: "Servicios",
      countries: "Países",
      changes: "Cambios",
      methodology: "Metodología",
    },
    sampleBanner:
      "Datos de ejemplo: estos precios son ilustrativos y no proceden de una ejecución real del agente.",
    footer: {
      disclaimer:
        "Información de precios y riesgos, no asesoría legal. Nunca recomendamos declarar datos falsos.",
      dataAsOf: (date: string, run: string | null) =>
        run ? `Datos al ${date} · ejecución ${run}` : `Datos al ${date} · todavía sin ejecuciones`,
      generatedAt: (when: string) => `Generado el ${when}`,
      methodologyLink: "Cómo se obtienen los datos",
      noAnalytics: "Sin analítica ni cookies.",
    },
  },

  common: {
    yes: "Sí",
    no: "No",
    unknown: "Desconocido",
    officialSite: "Web oficial",
    source: "Fuente",
    open: "Abrir",
    sourceLinkLabel: (kind: string, what: string) => `Fuente ${kind.toLowerCase()}: ${what}`,
    externalLink: "(enlace externo)",
    residenceTag: "Tu país",
    staleFx: "Tipo de cambio desactualizado",
    staleFxNote: "* Tipo de cambio desactualizado: se usó la última tabla disponible.",
    perMonth: "/mes",
    tableScrollHint: "Desliza la tabla horizontalmente para ver todas las columnas.",
    countryAndName: (flag: string, name: string) => `${flag} ${name}`.trim(),
  },

  periods: { month: "/mes", quarter: "/trimestre", year: "/año" } as Record<string, string>,
  periodNames: { month: "Mensual", quarter: "Trimestral", year: "Anual" } as Record<string, string>,

  channels: { web: "Web", ios_iap: "App Store" } as Record<string, string>,
  channelTitles: {
    web: "Precio publicado en la web oficial del servicio",
    ios_iap:
      "Precio de la compra dentro de la app en el App Store; puede incluir un recargo frente a la web",
  } as Record<string, string>,

  sourceKinds: { official: "Oficial", secondary: "Secundaria" } as Record<string, string>,

  extraction: {
    parser: "Parser",
    llm: "LLM (cita literal)",
  } as Record<string, string>,

  offerStatus: {
    accepted: "Aceptado",
    quarantined: "En revisión",
    rejected: "Rechazado",
    carried_forward: "Valor anterior",
  } as Record<string, string>,
  offerStatusTitles: {
    quarantined: "Cambio grande pendiente de confirmar; no se usa en el comparador",
    rejected: "Descartado por la validación; no se usa en el comparador",
    carried_forward: "La fuente falló hoy; se conserva el último valor confirmado",
  } as Record<string, string>,

  flags: {
    iap_maybe_marked_up: "App Store: puede tener recargo",
    apple_billing: "Cobro a través de Apple",
    secondary_disagrees: "La fuente secundaria difiere",
    iap_below_web_stale: "App Store por debajo de la web (posible desfase)",
    iap_duplicate: "Compra in-app duplicada",
    taxes_not_included: "Impuestos no incluidos",
    quarantine_hold: "Cambio en revisión: se muestra el valor anterior",
  } as Record<string, string>,

  categories: {
    video: "Vídeo",
    music: "Música",
    cloud: "Almacenamiento en la nube",
    bundle: "Paquete",
    productivity: "Productividad",
    gaming: "Juegos",
    other: "Otros",
  } as Record<string, string>,

  regions: {
    nordics: "Países nórdicos",
    eu: "Unión Europea",
    europe_other: "Resto de Europa",
    latam: "Latinoamérica",
    north_america: "Norteamérica",
    asia: "Asia",
    africa: "África",
    mena: "Oriente Medio y norte de África",
  } as Record<string, string>,

  euGeoClass: {
    audiovisual_excluded: "Audiovisual: excluido del Reglamento de geobloqueo",
    copyright_art3_5_only: "Obras protegidas: solo arts. 3 y 5 del Reglamento",
    eservice_art4_1b: "Servicio electrónico tipo nube (art. 4.1.b): derecho UE no probado",
    software_unclear: "Software: encaje incierto en el Reglamento",
  } as Record<string, string>,

  /** Used when feasibility.json does not carry a label (or is older than the site). */
  riskLabels: {
    official_home: {
      name: "Oficial",
      description: "Precio del país donde vives. Sin conflicto con los términos del servicio.",
    },
    eu_right_untested: {
      name: "Derecho UE no probado",
      description:
        "Cumple los términos, pero depende de una interpretación del Reglamento (UE) 2018/302 que ningún tribunal ha confirmado.",
    },
    tos_breach_low_enforcement: {
      name: "Incumple ToS, sin control conocido",
      description:
        "Contradice los términos del servicio; no se conocen cancelaciones ni bloqueos por ello.",
    },
    tos_breach_enforced: {
      name: "Incumple ToS, con control activo",
      description: "El servicio verifica el país y cancela o bloquea cuentas que no cumplen.",
    },
    not_feasible: {
      name: "No factible",
      description: "No se puede conseguir de forma realista con tu perfil.",
    },
    unknown: {
      name: "Sin evaluar",
      description: "Todavía no hay análisis de factibilidad. Nunca se elige como opción.",
    },
  } as Record<string, { name: string; description: string }>,
  riskLevel: (rank: number) => `Nivel ${rank}`,
  euBadge: "UE",
  euBadgeTitle: (residence: string) =>
    `Podría existir un derecho bajo el Reglamento (UE) 2018/302 (no probado; precio neto + IVA de ${residence})`,

  axes: {
    legality: {
      name: "Legalidad",
      question: () => "¿Choca con alguna norma?",
      values: {
        lawful: "Legal",
        contestable_eu_right: "Derecho UE discutible",
        requires_misrepresentation: "Requiere declarar datos falsos",
        unknown: "Sin datos",
      },
    },
    tos: {
      name: "Términos del servicio",
      question: () => "¿Lo permiten los términos del servicio y se controla?",
      values: {
        compliant: "Cumple",
        breach_unenforced: "Incumple, sin control conocido",
        breach_enforced: "Incumple, con control activo",
        unknown: "Sin datos",
      },
    },
    practical: {
      name: "Practicidad",
      question: (residence: string) => `¿Se puede hacer desde ${residence}?`,
      values: {
        remote: "En remoto",
        presence_once: "Presencia en el país una vez",
        presence_periodic: "Presencia periódica",
        impossible: "Imposible",
        unknown: "Sin datos",
      },
    },
  } as Record<
    string,
    { name: string; question: (residence: string) => string; values: Record<string, string> }
  >,

  confidence: { low: "Baja", medium: "Media", high: "Alta" } as Record<string, string>,
  curatedStatus: {
    verified: "Verificado",
    unverified: "Sin verificar",
    expired: "Caducado",
  } as Record<string, string>,

  /** Where a feasibility record comes from (feasibility.json records[].provenance). */
  provenance: {
    curated: "Curado",
    agent_reviewed: "Revisado",
    agent_pending: "Pendiente de revisión",
  } as Record<string, string>,
  provenanceTitles: {
    curated: "Ficha escrita a mano; siempre prevalece sobre la investigación automática.",
    agent_reviewed:
      "Calculada a partir de hallazgos del agente de investigación que una persona aceptó.",
    agent_pending:
      "Calculada con hallazgos del agente que nadie ha revisado todavía. El comparador no la usa salvo que marques «Incluir hallazgos pendientes de revisión».",
  } as Record<string, string>,
  /** Source class of a cited page (records[].evidence[].kind). */
  evidenceKinds: {
    official: "oficial",
    press: "prensa",
    forum: "foro",
    aggregator: "agregador",
    user_report: "reporte de usuario",
  } as Record<string, string>,

  /** The evidence block of a feasibility record (FeasibilityTable). */
  feasibility: {
    colSource: "Fuente",
    researchedOn: (date: string) => `Datos del ${date}`,
    evidenceSummary: (count: number) => `Evidencia (${count})`,
    noEvidence: "Sin citas.",
    sourceKind: (kind: string) => `fuente ${kind}`,
    retrievedOn: (date: string) => `consultado el ${date}`,
    verifiedQuote: "cita verificada",
    unverifiedQuote: "cita sin verificar",
    evidenceLinkLabel: (host: string, kind: string) => `${host} (fuente ${kind})`,
    assumptionsTitle: "Supuestos",
    reasonsTitle: "Motivos",
  },

  coverageStatus: {
    ok: "OK",
    partial: "Parcial",
    failed: "Falló",
    not_available: "No disponible",
    blocked: "Bloqueado",
    robots_disallowed: "Prohibido por robots.txt",
    not_configured: "Sin configurar",
    wrong_country: "País equivocado",
  } as Record<string, string>,

  changeKinds: {
    new: "Nuevo",
    added: "Nuevo",
    changed: "Cambio de precio",
    price_change: "Cambio de precio",
    up: "Subida",
    increase: "Subida",
    down: "Bajada",
    decrease: "Bajada",
    removed: "Retirado",
    dropped: "Retirado",
    status: "Cambio de estado",
  } as Record<string, string>,

  bundle: {
    price: "Precio",
    monthlyEur: "Mensual (EUR)",
    standalone: "Por separado",
    saving: "Ahorro",
    marginal: (amount: string) => `Coste adicional sobre el plan base: ${amount}`,
    components: "Incluye",
    requirements: "Requisitos",
    feasibility: (residence: string) => `Desde ${residence}`,
    lastVerified: (date: string) => `Última verificación: ${date}`,
    status: "Estado",
    noPrice: "Precio sin confirmar",
    officialPage: "Página oficial",
    inclusion: {
      included: () => "Incluido",
      discount_pct: (v: string) => `${v} de descuento`,
      discount_abs: (v: string) => `${v} de descuento`,
      free_months: (v: string) => `${v} meses gratis`,
      choose_n: () => "A elegir",
    } as Record<string, (value: string) => string>,
    withAds: "con anuncios",
    taxIncluded: "IVA incluido",
    taxExcluded: "IVA no incluido",
    feasibilityFields: {
      purchasable_remotely: "Contratable en remoto",
      usable_abroad: "Usable en el extranjero",
      tos_risk: "Riesgo de términos",
      evidence: "Evidencia",
    } as Record<string, string>,
    feasibilityValues: {
      yes: "Sí",
      no: "No",
      unknown: "Desconocido",
      travel_only: "Solo de viaje",
      low: "Bajo",
      medium: "Medio",
      high: "Alto",
      user_report: "Reporte de usuario",
      terms: "Términos",
      press: "Prensa",
      none: "Ninguna",
    } as Record<string, string>,
    requirementFields: {
      residency: "Residencia",
      local_id: "Documento local",
      local_line: "Línea local",
      local_payment: "Pago",
      credit_check: "Estudio de crédito",
    } as Record<string, string>,
    requirementValues: {
      unknown: "Desconocido",
      none: "No",
      mobile: "Móvil",
      fixed: "Fija",
      both: "Móvil y fija",
      any_card: "Cualquier tarjeta",
      local_card: "Tarjeta local",
      local_bank: "Cuenta bancaria local",
      carrier_bill: "Factura del operador",
    } as Record<string, string>,
  },

  home: {
    title: "Comparador de suscripciones",
    lead: (residence: string) =>
      `Compara lo que pagas por tus suscripciones en ${residence} con su precio oficial en otros países, teniendo en cuenta el riesgo de cada opción y la comisión por pagar en otra moneda.`,
    howTitle: "Cómo leer el resultado",
    howItems: [
      "El precio de cada país es el oficial publicado por el servicio, convertido a la moneda que elijas.",
      "Solo se elige una oferta si su nivel de riesgo es igual o menor que el que aceptas. Las ofertas sin evaluar nunca se eligen.",
      "El ahorro ya descuenta la comisión de cambio por pagar en otra moneda.",
      "Antes de cambiar nada, lee la ficha del servicio y la metodología.",
    ],
  },

  compare: {
    servicesLegend: "Servicios",
    servicesHelp: "Marca los servicios que pagas y elige el plan.",
    planLabel: (service: string) => `Plan de ${service}`,
    riskLegend: "Riesgo máximo aceptado",
    riskHelp:
      "Solo se eligen ofertas con un nivel de riesgo igual o menor. Las ofertas sin evaluar nunca se eligen.",
    currencyLegend: "Moneda",
    profileLegend: "Perfil",
    profileHelp:
      "Las etiquetas de riesgo dependen de lo que puedes demostrar honestamente: residencia, documentos, teléfono, dirección, medios de pago y viajes.",
    profileLabel: "Perfil de factibilidad",
    pendingLabel: "Incluir hallazgos pendientes de revisión",
    pendingHelp:
      "Hallazgos del agente de investigación que nadie ha revisado todavía. Sin marcar, solo cuentan las fichas curadas o revisadas.",
    pendingNote:
      "El resultado incluye hallazgos pendientes de revisión: todavía nadie los ha comprobado.",
    loading: "Cargando precios…",
    noscript:
      "El comparador necesita JavaScript. Sin él puedes consultar los precios por servicio o por país.",
    noneSelected: "Elige al menos un servicio para ver la comparación.",
    resultsTitle: "Resultado",
    tableCaption: (residence: string, risk: string) =>
      `Precio mensual en ${residence} frente a la oferta más barata con riesgo hasta «${risk}»`,
    colService: "Servicio",
    colHome: (residence: string) => `En ${residence}`,
    colBest: "Oferta más barata aceptable",
    colMonthly: "Mensual",
    colSaving: "Ahorro mensual",
    noHome: "Sin precio local",
    noOffer: "Sin oferta con este riesgo",
    homeIsBest: "Tu país ya es la opción más barata",
    localPrice: (price: string) => `Precio local: ${price}`,
    totalsTitle: "Totales",
    totalsCaption: "Totales de los servicios comparables",
    rowMonthly: "Mensual",
    rowYearly: "Anual",
    colPayHome: (residence: string) => `Pagando en ${residence}`,
    colOptimized: "Optimizado",
    colNetSaving: "Ahorro neto",
    feeNote: (fee: string, amount: string) =>
      `El total optimizado incluye ${amount} al mes de comisión de cambio (${fee}) por pagar en otra moneda.`,
    excludedNote: (names: string) => `No se suman en los totales (sin precio local): ${names}.`,
    noOfferNote: (names: string) => `Sin oferta con este nivel de riesgo: ${names}.`,
    savingsByRiskTitle: "Ahorro mensual según el riesgo aceptado",
    savingsByRiskHelp: "Con la misma selección, cuánto ahorrarías al mes en cada nivel.",
    fxNote: (date: string) => `Conversión con los tipos de cambio del ${date}.`,
    shareNote: "La selección se guarda en la dirección de la página, así puedes compartirla.",
    servicePageLink: (name: string) => `Ver todos los precios de ${name}`,
    summaryLive: (saving: string, yearly: string) =>
      `Ahorro neto estimado: ${saving} al mes, ${yearly} al año.`,
  },

  servicesIndex: {
    title: "Servicios",
    lead: "Todos los servicios con precios recogidos o que aparecen en algún paquete.",
    caption:
      "Servicios y oferta más barata del plan por defecto según el riesgo aceptado (EUR al mes, con comisión de cambio)",
    colService: "Servicio",
    colCategory: "Categoría",
    colCountries: "Países con precio",
    colDefaultPlan: "Plan por defecto",
    colHome: (residence: string) => `En ${residence}`,
    colRisk: (rank: number) => `Riesgo ≤ ${rank}`,
  },

  service: {
    homepage: "Web oficial",
    defaultPlan: "Plan por defecto",
    category: "Categoría",
    geoClass: "Reglamento de geobloqueo",
    summaryTitle: "Más barato por nivel de riesgo",
    summaryCaption: (plan: string) =>
      `Oferta más barata del plan ${plan} por nivel de riesgo, en EUR al mes con comisión de cambio`,
    colRisk: "Riesgo máximo",
    colOffer: "Oferta",
    colCost: "EUR/mes",
    colSaving: (residence: string) => `Ahorro frente a ${residence}`,
    offersTitle: "Precios por país",
    offersCaption: (name: string) =>
      `Todas las ofertas de ${name}, ordenadas por precio mensual en EUR`,
    convNote: (date: string) =>
      `«Mensual» divide el precio entre los meses del periodo y lo convierte con los tipos de cambio del ${date}, sin comisión.`,
    colCountry: "País",
    colPlan: "Plan",
    colLocal: "Precio local",
    colMonthlyIn: (currency: string) => `Mensual (${currency})`,
    colChannel: "Canal",
    colVat: "IVA incluido",
    colSource: "Fuente",
    colRiskLabel: "Riesgo",
    colUpdated: "Última actualización",
    updatedNote: (date: string) =>
      `«Última actualización» es la fecha del último cambio de precio; todos se comprobaron el ${date}.`,
    noOffers: "Todavía no hay una fuente de precios para este servicio; aparece por los paquetes.",
    bundlesTitle: "Paquetes que lo incluyen",
    noBundles: "No hay paquetes registrados que incluyan este servicio.",
    feasibilityTitle: (residence: string) => `Factibilidad desde ${residence}`,
    feasibilityLead: (residence: string, profile?: string) =>
      `Fichas para quien vive en ${residence}${profile ? ` (perfil «${profile}»)` : ""}. Las curadas a mano son semillas conservadoras, casi todas sin verificar; las del agente de investigación citan sus fuentes y, hasta que una persona las revisa, figuran como «Pendiente de revisión» y el comparador no las usa.`,
    noFeasibility: (residence: string) =>
      `No hay fichas de factibilidad para este servicio: fuera de ${residence} sus ofertas figuran como «Sin evaluar».`,
    colTarget: "País objetivo",
    colLabel: "Etiqueta",
    colConfidence: "Confianza",
    colStatus: "Estado",
    colNotes: "Notas",
    tosClause: "Cláusula de los términos",
    notesTitle: "Notas",
    backToServices: "Todos los servicios",
  },

  countriesIndex: {
    title: "Países",
    lead: "Países del catálogo con su moneda y el número de ofertas publicadas.",
    caption: "Países, moneda y número de ofertas",
    colCountry: "País",
    colCurrency: "Moneda",
    colRegion: "Región",
    colEu: "UE/EEE",
    colOffers: "Ofertas",
  },

  country: {
    currency: "Moneda",
    region: "Región",
    eu: "UE/EEE",
    offersTitle: "Ofertas",
    offersCaption: (name: string) => `Todas las ofertas en ${name}, por servicio y plan`,
    colService: "Servicio",
    noOffers: "Todavía no hay precios publicados para este país.",
    bundlesTitle: "Paquetes",
    noBundles: "No hay paquetes registrados en este país.",
    feasibilityTitle: (residence: string) => `Factibilidad desde ${residence}`,
    noFeasibility: "No hay fichas de factibilidad para este país.",
    backToCountries: "Todos los países",
  },

  changes: {
    title: "Cambios de precio",
    lead: "Cambios detectados en los últimos 90 días, del más reciente al más antiguo.",
    caption: "Cambios de precio de los últimos 90 días",
    colDate: "Fecha",
    colService: "Servicio",
    colPlan: "Plan",
    colCountry: "País",
    colChannel: "Canal",
    colKind: "Tipo",
    colOld: "Antes",
    colNew: "Después",
    colPct: "Variación",
    empty: "No hay cambios en los últimos 90 días.",
  },

  methodology: {
    title: "Metodología",
    lead: "Qué datos mostramos, de dónde salen y qué riesgos conviene entender antes de usarlos.",
    toc: "En esta página",
    collectTitle: "Cómo se recogen los datos",
    collect: [
      {
        title: "Fuentes oficiales primero",
        text: "Cada día un agente visita las páginas oficiales de cada servicio para cada país: la ayuda de Netflix, la web de Spotify, la página de soporte de Apple con los precios de iCloud+, las páginas de Apple One, Apple Music y Apple TV y el catálogo de Microsoft Store. Respeta robots.txt, se identifica con un User-Agent propio, hace como máximo una petición cada 3 segundos por sitio y nunca inicia sesión ni resuelve CAPTCHAs.",
      },
      {
        title: "App Store como respaldo",
        text: "Cuando la web oficial muestra el precio según la IP del visitante (YouTube Premium, Google One, HBO Max) o su robots.txt no permite el acceso a agentes como este (Disney+), se usa la lista pública de compras dentro de la app del App Store de cada país. Esas filas llevan el canal «App Store» y la marca «puede tener recargo»: suelen ser más caras que la web.",
      },
      {
        title: "Lectura determinista; el LLM solo como respaldo",
        text: "Los precios se extraen con parsers deterministas (datos estructurados de la página, selectores y texto junto al nombre del plan). Solo si el parser falla, un modelo de lenguaje lee la página guardada y debe citar literalmente el texto del precio; el código comprueba que la cita existe en la página, que la moneda es la del país y que el importe es plausible. El modelo nunca calcula ni convierte importes, y esas filas se marcan como «LLM».",
      },
      {
        title: "Validación y valores anteriores",
        text: "Se descartan importes en otra moneda o fuera de un rango plausible. Un cambio de más del 30 % queda «en revisión» hasta confirmarse en la siguiente ejecución y no se usa en el comparador. Si una página falla, se conserva el último valor confirmado durante unos días («valor anterior»).",
      },
      {
        title: "Tipos de cambio y comisión",
        text: "Cada moneda se convierte con el tipo de su banco central (vía Frankfurter) cuando está fijado: peso colombiano, argentino y chileno, libra egipcia y naira. Las demás usan el Banco Central Europeo y, como respaldo, Frankfurter y ExchangeRate-API. Si todos fallan se usa la última tabla disponible marcada como desactualizada. Las conversiones son orientativas: el comparador añade una comisión de cambio a toda oferta en una moneda distinta de la de tu país.",
      },
    ],
    feeLine: (fee: string) => `Comisión de cambio usada en el comparador: ${fee}.`,
    axesTitle: "Tres ejes de riesgo",
    axesLead:
      "Cada ficha de factibilidad evalúa por separado tres ejes. La etiqueta final es la del peor eje: un nivel alto en cualquiera de ellos no se compensa con los otros dos.",
    axesRules: [
      "Si algún paso exige declarar datos falsos (por ejemplo, una dirección de otro país), la etiqueta es como mínimo de nivel 2.",
      "Los precios de tu país de residencia son siempre «Oficial» (nivel 0).",
      "Sin ficha, la oferta queda «Sin evaluar» y el comparador nunca la elige.",
      "El distintivo «UE» es secundario: indica que podría existir un derecho bajo el Reglamento (UE) 2018/302, no que sea seguro.",
      "Un eje «Sin datos» significa que faltan hechos con fuente: la oferta queda «Sin evaluar».",
    ],
    axisValueCaption: (axis: string) => `Valores del eje ${axis}`,
    labelsTitle: "Etiquetas",
    labelsCaption: "Etiquetas de riesgo y su nivel",
    colLevel: "Nivel",
    colLabel: "Etiqueta",
    colDescription: "Qué significa",
    researchTitle: "Investigación de factibilidad",
    researchLead:
      "Además de las fichas escritas a mano, un agente investiga qué exige cada servicio para contratar la oferta de otro país.",
    research: [
      {
        title: "El agente solo descubre",
        text: "Cada semana un agente busca con Google Search páginas oficiales, prensa y foros sobre las reglas de cada servicio: país de la cuenta, medio de pago, dirección, controles. Solo propone hallazgos; nada de lo que dice cuenta por sí mismo.",
      },
      {
        title: "Cada dato necesita una cita literal comprobada",
        text: "Un hallazgo solo cuenta si nuestra propia descarga de la página citada encuentra la cita literal. Esa descarga respeta robots.txt y nunca intenta saltarse un bloqueo, un inicio de sesión ni un CAPTCHA: si la página no se puede leer, el dato no cuenta. Los foros nunca cuentan y la prensa solo para restricciones.",
      },
      {
        title: "Hechos comunes, veredictos por perfil",
        text: "Los hechos no dependen de quién compra; el veredicto sí. Un evaluador determinista cruza los hechos con lo que cada perfil puede demostrar honestamente y calcula los tres ejes. Si falta un dato, el eje queda «Sin datos» y la oferta, «Sin evaluar».",
      },
      {
        title: "Lo curado a mano manda",
        text: "Si existe una ficha escrita a mano para un servicio, un país y un perfil, se usa esa y no la del agente.",
      },
      {
        title: "Revisión humana",
        text: "Los hallazgos nuevos figuran como «Pendiente de revisión» hasta que una persona los acepta. El comparador no los usa salvo que marques «Incluir hallazgos pendientes de revisión»; en las fichas se ven con su distintivo y sus citas.",
      },
      {
        title: "Nunca datos falsos",
        text: "Una vía que exigiría declarar algo falso, como una dirección o un país que no son los tuyos, se muestra como «No factible». El sitio nunca sugiere usar una VPN ni dar datos falsos.",
      },
    ],
    researchRun: (date: string, run: string | null) =>
      run
        ? `Última investigación publicada: ${date} (ejecución ${run}).`
        : "Todavía no se ha publicado ninguna investigación: solo hay fichas escritas a mano.",
    profilesTitle: "Perfiles",
    profilesLead:
      "Cada perfil describe lo que una persona puede demostrar honestamente: residencia, documentos, teléfono, dirección, medios de pago y viajes. Las fichas de servicios y países muestran el perfil por defecto; cuando hay más de uno, el comparador tiene un selector de perfil.",
    defaultProfileTag: "Por defecto",
    researchNotLegal:
      "No es asesoría legal: describe lo que encontramos en fuentes públicas, no lo que debas hacer.",
    assumptionsTitle: "Cinco supuestos arriesgados",
    assumptionsLead: "Errores frecuentes al razonar sobre precios de otros países:",
    assumptions: (residence: string) => [
      {
        title: "Una tarjeta extranjera no te convierte en cliente extranjero",
        text: "Pagar con una tarjeta de otro país no te hace residente de ese país. La mayoría de los servicios atan la cuenta al país donde vives o donde te registraste, no al banco que emitió la tarjeta.",
      },
      {
        title: "Que no se conozcan controles no significa que sea seguro",
        text: "«Sin control conocido» solo describe lo encontrado en fuentes públicas. Un servicio puede empezar a verificar el país, cancelar la suscripción o bloquear la cuenta en cualquier momento.",
      },
      {
        title: "El derecho UE solo cubre servicios tipo nube y no está probado",
        text: `El Reglamento (UE) 2018/302 excluye los servicios audiovisuales y, en la práctica, la música y los contenidos protegidos. Para servicios tipo nube como iCloud+ o Google One podría aplicarse, pero el precio sería el neto del otro país más el IVA de ${residence}, y ningún tribunal lo ha confirmado.`,
      },
      {
        title: "Los precios y las comprobaciones de cuenta cambian",
        text: "Los precios suben, los planes se renombran y las verificaciones de país se endurecen. Revisa la fecha de cada precio y la ficha de factibilidad antes de decidir.",
      },
      {
        title: "Cambiar no siempre es reversible",
        text: "Cambiar el país de una cuenta de Apple, Google o Microsoft afecta a toda la tienda, puede hacerte perder saldo, compras o el grupo familiar, y algunos cambios solo se permiten una vez al año.",
      },
    ],
    coverageTitle: "Cobertura",
    coverageLead: (date: string) =>
      `Estado de cada fuente en la última ejecución (${date}). «Falla desde» indica desde cuándo una fuente no devuelve precios.`,
    coverageCaption: "Resumen de cobertura por servicio",
    coverageDetail: "Ver el detalle por país y canal",
    coverageDetailCaption: "Cobertura por servicio, país y canal",
    colService: "Servicio",
    colTotal: "Total",
    colCountry: "País",
    colChannel: "Canal",
    colStatus: "Estado",
    colAdapter: "Adaptador",
    colFailingSince: "Falla desde",
    coverageEmpty: "No hay datos de cobertura.",
    attributionsTitle: "Atribuciones",
    attributionsLead: "Datos de terceros usados con su licencia:",
    license: (license: string) => `Licencia: ${license}`,
    disclaimerTitle: "No es asesoría legal",
    disclaimer: (profile: string) => [
      "Este sitio informa de precios y de riesgos. No es asesoría legal, fiscal ni financiera, y no recomienda incumplir los términos de ningún servicio ni declarar datos falsos.",
      `Las fichas de factibilidad son semillas conservadoras, casi todas sin verificar, escritas para un perfil concreto (${profile}). Pueden no aplicarse a tu caso.`,
      "Decide bajo tu responsabilidad y consulta siempre los términos vigentes de cada servicio.",
    ],
    axisPoints: "Nivel",
    axisValue: "Valor",
    tompecFallback: {
      name: "Netflix prices dataset, Thomas Pecserke (tompec/netflix-prices)",
      url: "https://github.com/tompec/netflix-prices",
      license: "CC BY 4.0",
    },
  },

  /** meta.profile -> who the feasibility records were written for (residence from meta). */
  profiles: {
    se_resident_co_citizen: (residence: string) =>
      `residente en ${residence} con ciudadanía y tarjeta colombianas`,
  } as Record<string, (residence: string) => string>,
  unknownProfile: (id: string, residence: string) => `perfil «${id}», residente en ${residence}`,

  notFound: {
    title: "Página no encontrada",
    text: "La página que buscas no existe o cambió de dirección.",
    back: "Volver al comparador",
  },

  errors: {
    missingData: (file: string) =>
      `Falta web/src/data/${file}.json. Genera los datos con "uv run haggler site-data" o usa los de ejemplo con "npm run sample-data".`,
    invalidData: (problems: string[]) =>
      [
        "Los datos de web/src/data/ no cumplen el contrato de web/src/logic/types.ts (¿cambió src/haggler/publish.py sin actualizar types.ts y validate.ts?):",
        ...problems.map((p) => `  - ${p}`),
      ].join("\n"),
  },
};

export type Strings = typeof es;

export function profileDescription(id: string, residence: string): string {
  return es.profiles[id]?.(residence) ?? es.unknownProfile(id, residence);
}

export function riskLabelName(id: string): string {
  return es.riskLabels[id]?.name ?? id;
}

export function lookup(table: Record<string, string>, key: string | null | undefined): string {
  if (key === null || key === undefined) return es.common.unknown;
  return table[key] ?? key;
}
