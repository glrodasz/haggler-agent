/**
 * Spanish copy. Same shape as en.ts (the default locale), which the type below enforces.
 */

import type { Messages } from "./en";

export const es = {
  site: {
    name: "Haggler",
    tagline: "Precios de suscripciones por país, con su riesgo",
    description: (residence: string) =>
      `Compara los precios oficiales de Netflix, Spotify, iCloud+ y más en cada país, con el riesgo de cada opción si vives en ${residence}.`,
    /** Social preview card (public/og/haggler.png, drawn by scripts/brand-assets.mjs). */
    ogImageAlt: "Haggler: precios de suscripciones por país, con su riesgo.",
    seo: {
      serviceTitle: (name: string) => `${name}: precio en cada país`,
      serviceDescription: (name: string, countries: number) =>
        `Precio oficial de ${name} en ${countries} ${countries === 1 ? "país" : "países"}, convertido a tu moneda, con el riesgo de contratarlo desde fuera de tu país de residencia.`,
      countryTitle: (name: string) => `Precios de suscripciones en ${name}`,
      countryDescription: (name: string, currency: string) =>
        `Precios oficiales de Netflix, Spotify, iCloud+ y otras suscripciones en ${name} (${currency}), paquetes locales y el riesgo de contratarlos desde fuera.`,
    },
    skipToContent: "Saltar al contenido",
    navLabel: "Principal",
    nav: {
      home: "Comparador",
      services: "Servicios",
      countries: "Países",
      bundles: "Paquetes",
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
    pageEyebrow: (data: string) => `Haggler · ${data}`,
    sampleData: "Datos de ejemplo",
    dataAsOfShort: (date: string) => `Datos al ${date}`,
    backToCompare: "Volver al comparador",
  },

  language: {
    menuLabel: "Idioma",
    switchTo: "Cambiar a español",
    spanishOnly: "En español",
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
    home: "Tu país",
    curated: "Curado",
    composed: "Por requisitos",
    template: "Por analogía",
    agent_reviewed: "Revisado",
    agent_pending: "Pendiente de revisión",
  } as Record<string, string>,
  provenanceTitles: {
    home: "Oferta del país donde vives, evaluada con los requisitos que exige.",
    curated: "Ficha escrita a mano; siempre prevalece sobre la investigación automática.",
    composed:
      "Calculado a partir de los requisitos del paquete y del veredicto de cada servicio incluido.",
    template:
      "Mismos términos y pasos que la ficha curada del servicio, aplicados a este país sin investigarlo en concreto.",
    agent_reviewed:
      "Calculada a partir de hallazgos del agente de investigación que una persona aceptó.",
    agent_pending:
      "Calculada con hallazgos del agente que nadie ha revisado todavía. El comparador la usa, marcada como pendiente, hasta que una persona la revise.",
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
    fullMonthlyEur: "Completo (EUR)",
    marginalEur: "Adicional (EUR)",
    standalone: "Por separado",
    saving: "Ahorro",
    /** The line under the full price of a bundle sold on top of a base plan. */
    marginalLine: (basePlan: string, amount: string) => `Si ya pagas ${basePlan}: +${amount}`,
    basePlanFallback: "el plan base",
    basePlanWithPrice: (name: string, price: string) => `${name} (${price})`,
    observedOn: (service: string, date: string) => `Precio de ${service} publicado el ${date}`,
    observedMissing: (service: string) => `Sin precio de ${service} en la última ejecución.`,
    introLine: (amount: string, months: number) =>
      `Precio de introducción: ${amount} durante ${months} ${months === 1 ? "mes" : "meses"}`,
    introUntil: (date: string) => `para altas hasta el ${date}`,
    firstYear: "Primer año (EUR)",
    firstYearValue: (amount: string) => `${amount} de media al mes`,
    contract: (months: number) => `Permanencia: ${months} ${months === 1 ? "mes" : "meses"}`,
    withFee: (amount: string, fee: string) => `${amount} incl. comisión de cambio ${fee}`,
    partialSum: (names: string) => `suma parcial: sin precio por separado de ${names}`,
    noStandalone: (names: string) => `Sin precio por separado de ${names}.`,
    unverifiedSaving: "sin verificar",
    marginalSaving: "sobre el coste adicional",
    savingHelp:
      "Frente a comprar por separado los servicios incluidos, en el país del paquete. No cuenta la comisión de cambio, los precios de introducción, los descuentos ni los meses gratis.",
    optionalTitle: "Si además contratas",
    optionalHelp: "Descuentos y meses gratis sobre otros servicios; no se suman al ahorro.",
    optionalSaving: (amount: string) => `≈ −${amount}/mes`,
    optionalNoPrice: "sin precio de referencia",
    freeMonthsNote: "no se reparte como ahorro mensual",
    components: "Incluye",
    standaloneEach: (amount: string) => `${amount}/mes por separado`,
    confirmed: "confirmado",
    unconfirmed: "sin confirmar",
    confirmedTitle: "Lo encontramos en una página oficial del país del paquete.",
    unconfirmedTitle: "Todavía no lo hemos encontrado en una página oficial del país del paquete.",
    requirements: "Requisitos",
    lastVerified: (date: string) => `Última revisión: ${date}`,
    noPrice: "Precio sin confirmar",
    officialPage: "Página oficial",
    inclusion: {
      included: () => "Incluido",
      discount_pct: (v: string) => `${v} de descuento`,
      discount_abs: (v: string) => `${v} de descuento`,
      free_months: (v: string) => (v === "1" ? "1 mes gratis" : `${v} meses gratis`),
      choose_n: (n: string) => (n ? `A elegir (${n} del grupo)` : "A elegir"),
    } as Record<string, (value: string) => string>,
    withAds: "con anuncios",
    taxIncluded: "IVA incluido",
    taxExcluded: "IVA no incluido",
    /** display_status: derived from the watcher, a manual check and the dates, never typed. */
    displayStatus: {
      verified: "Verificado",
      needs_review: "Requiere revisión",
      manual: "Verificación manual",
      unverified: "Sin verificar",
      expired: "Vencida",
    } as Record<string, string>,
    displayStatusTitles: {
      verified:
        "Precio y componentes encontrados hace 15 días o menos en una página oficial del país del paquete.",
      needs_review:
        "La página oficial ya no muestra algún valor registrado, muestra uno distinto o publicó un documento nuevo: falta revisarlo.",
      manual: "Una persona lo comprobó a mano hace 45 días o menos. No cuenta como fuente oficial.",
      unverified: "Ningún valor está confirmado en una página oficial.",
      expired: "La oferta terminó; se muestra solo como referencia.",
    } as Record<string, string>,
    verifiedOn: (date: string) => `comprobado el ${date}`,
    manualAgo: (days: number) =>
      days === 0 ? "hoy" : `hace ${days} ${days === 1 ? "día" : "días"}`,
    checkedOn: (date: string) => `última comprobación: ${date}`,
    expiredOn: (date: string) => `terminó el ${date}`,
    validFrom: (date: string) => `Disponible desde el ${date}`,
    validUntil: (date: string) => `Disponible hasta el ${date}`,
    statusReasons: "Por qué",
    forProfile: "Para tu perfil",
    otherProfiles: (count: number) => `Otros perfiles (${count})`,
    pendingVariant: "Con hallazgos pendientes de revisión",
    noVerdict: "Sin veredicto para este perfil.",
    expiredNoVerdict: "Oferta vencida: no se evalúa.",
    verdictStatus: "Estado de la ficha",
    verdictConfidence: "Confianza",
    perComponent: "Por servicio incluido",
    evidenceSummary: (count: number) => `Fuentes (${count})`,
    evidenceFacts: {
      price: "precio",
      base_price: "plan base",
      intro: "precio de introducción",
      component: "servicio incluido",
      validity: "vigencia",
    } as Record<string, string>,
    evidenceVia: {
      embedded: "dato embebido",
      embedded_only: "dato embebido",
      text: "texto visible",
      visible: "texto visible",
      pdf: "documento PDF",
    } as Record<string, string>,
    evidenceEdition: (edition: string) => `edición ${edition}`,
    evidenceStatus: {
      verified: "encontrada",
      missing: "no encontrada",
      unreadable: "página ilegible",
      embedded_only: "solo en datos embebidos",
      outdated_document: "documento desactualizado",
      not_checked: "sin comprobar",
    } as Record<string, string>,
    /** pending_changes: official values that differ from the curated data, awaiting review. */
    pendingTitle: (count: number) => `Cambios pendientes de revisión (${count})`,
    pendingLead:
      "Valores que una página oficial del país del paquete indica y los datos curados aún no. No cambian nada hasta que una persona los revisa y edita los datos.",
    pendingFields: {
      bundle_price: "Precio",
      bundle_intro_price: "Precio de introducción",
      bundle_base_price: "Precio del plan base",
      bundle_intro_months: "Meses del precio de introducción",
      bundle_contract_months: "Permanencia",
      bundle_intro_until: "Fin del precio de introducción",
      bundle_offer_until: "Fin de la oferta",
      bundle_includes: "Servicio incluido",
      bundle_discount: "Descuento",
      bundle_available: "Se sigue vendiendo",
      bundle_price_list_url: "Lista de precios",
    } as Record<string, string>,
    pendingSources: {
      watcher: "el vigilante",
      agent: "la investigación automática",
    } as Record<string, string>,
    pendingNone: "sin registrar",
    pendingChange: (current: string, proposed: string) => `${current} → ${proposed}`,
    pendingMonths: (months: number) => `${months} ${months === 1 ? "mes" : "meses"}`,
    pendingDiscount: (service: string, pct: string) => `${pct} en ${service}`,
    pendingSeen: (source: string, date: string) => `según ${source}, ${date}`,
    pendingBigChange: "cambio grande",
    pendingBigChangeTitle: "Más del 50 % de diferencia con el valor registrado.",
    /** watch_mode: how the bundle's official pages are checked. */
    watchModes: {
      auto: "Automática",
      manual: "A mano",
      off: "Sin vigilancia",
    } as Record<string, string>,
    watchModeTitles: {
      auto: "Sus páginas oficiales están en la vigilancia automática semanal; si aún no se han leído, la ficha lo indica.",
      manual: "Sus páginas no se leen de forma automática: una persona las revisa.",
      off: "Sus valores no se comprueban de forma automática.",
    } as Record<string, string>,
    expiredTitle: (count: number) => `Ofertas vencidas (${count})`,
    expiredLead: "Paquetes cuya oferta ya terminó. Se muestran un tiempo como referencia.",
    requirementFields: {
      residency: "Residencia en el país",
      billing_address: "Dirección de facturación en el país",
      signup_presence: "Presencia en el país al contratar",
      local_id: "Documento local",
      local_line: "Línea local",
      local_payment: "Pago",
      credit_check: "Estudio de crédito",
      new_customers_only: "Solo clientes nuevos",
    } as Record<string, string>,
    requirementValues: {
      unknown: "Desconocido",
      required: "Se exige",
      not_required: "No se exige",
      checked: "Se comprueba",
      not_checked: "No se comprueba",
      none: "No",
      mobile: "Móvil",
      fixed: "Fija",
      both: "Móvil y fija",
      any_card: "Cualquier tarjeta",
      local_card: "Tarjeta local",
      local_bank: "Cuenta bancaria local",
      carrier_bill: "Factura del operador",
      cedula: "Cédula",
      personnummer: "Personnummer",
    } as Record<string, string>,
  },

  home: {
    title: "Comparador de suscripciones",
    eyebrow: "Precios oficiales, país por país",
    heroTitle: "Paga lo justo por tus suscripciones",
    heroAccent: "lo justo",
    /** The savings tags under the hero title (islands/HeroSavings.tsx), all at level 3. */
    teasers: {
      label: (residence: string) => `Ahorro a nivel 3 viviendo en ${residence}`,
      headline: (pct: string, countries: string) => `Ahorra ${pct} en ${countries}`,
      alone: "Sin vínculos con otros países",
      aloneTag: "sin vínculos",
      tie: (country: string) => `Con documento, tarjeta y viajes regulares a ${country}`,
      linkTitle: "Ver este cálculo en el comparador",
    },
    lead: (residence: string) =>
      `El mismo servicio. Otro país. Un precio distinto. Compara lo que pagas por tus suscripciones en ${residence} con su precio oficial en otros países. Te decimos cuánto ahorrarías, con qué riesgo y cuánto se lleva la comisión de cambio.`,
    ctaStart: "Calcular mi ahorro",
    ctaMethod: "Cómo lo calculamos",
    stats: {
      services: (n: number) => `${n} servicios`,
      countries: (n: number) => `${n} países`,
      asOf: (date: string) => `Datos al ${date}`,
    },
    workflowTitle: "Cómo trabaja el agente",
    workflow: [
      { title: "Explora precios", text: "País por país" },
      { title: "Evalúa el riesgo", text: "Sin letra pequeña" },
      { title: "Calcula tu ahorro", text: "Comisiones incluidas" },
    ],
    trust: ["Sin registro", "Sin cookies ni analítica", "Nunca te pedimos datos falsos"],
    stepsTitle: "Tres pasos, un minuto",
    stepsLead: "No hace falta registrarse. Todo se calcula en tu navegador.",
    steps: [
      {
        title: "Elige lo que pagas",
        text: "Marca tus suscripciones y el plan de cada una.",
      },
      {
        title: "Cuéntanos tu situación",
        text: "Tu perfil, el riesgo que aceptas y la moneda en la que piensas.",
      },
      {
        title: "Mira tu ahorro real",
        text: "La oferta más barata que encaja contigo, ya con la comisión de cambio.",
      },
    ],
    compareEyebrow: "Tu comparación",
    compareTitle: "Calcula tu ahorro paso a paso",
    compareLead: "Cada cambio actualiza el resultado al instante.",
    howTitle: "Sin letra pequeña",
    howLink: "Cómo leer el resultado",
    howItems: [
      {
        title: "Precios oficiales, no atajos",
        text: "El precio de cada país es el que publica el servicio, convertido a la moneda que elijas.",
      },
      {
        title: "El riesgo lo eliges tú",
        text: "Solo se elige una oferta si su nivel de riesgo es igual o menor que el que aceptas. Las ofertas sin evaluar nunca se eligen.",
      },
      {
        title: "El ahorro es neto",
        text: "Ya descuenta la comisión de cambio por pagar en otra moneda.",
      },
      {
        title: "Tus datos se quedan contigo",
        text: "Todo se calcula en tu navegador. Antes de cambiar nada, lee la ficha del servicio y la metodología.",
      },
    ],
  },

  compare: {
    servicesLegend: "Tus suscripciones",
    servicesHelp: "Marca los servicios que pagas y elige el plan.",
    planLabel: (service: string) => `Plan de ${service}`,
    riskLegend: "Riesgo que aceptas",
    riskHelp:
      "Solo se eligen ofertas con un nivel de riesgo igual o menor. Las ofertas sin evaluar nunca se eligen.",
    currencyLegend: "Moneda",
    currencyLabel: "Moneda para mostrar los importes",
    currencyCommon: "Frecuentes",
    currencyAll: "Todas las monedas",
    profileLegend: "Tu perfil",
    profileHelp:
      "Las etiquetas de riesgo dependen de lo que puedes demostrar honestamente: dónde vives, documentos, teléfono, dirección, medios de pago y viajes.",
    profileLabel: "Perfil de factibilidad",
    residenceLabel: "1. ¿En qué país vives?",
    residenceHelp: (host: string, byIp: boolean) =>
      byIp
        ? `Solo listamos países cuyos precios seguimos. Para empezar por tu país, tu navegador pregunta a ${host} el país de tu IP; no guardamos nada.`
        : `Solo listamos países cuyos precios seguimos. «Usar mi IP» envía tu IP a ${host}; solo si lo pulsas.`,
    locateFound: {
      timezone: "Lo dedujimos de la zona horaria de tu navegador.",
      language: "Lo dedujimos del idioma de tu navegador.",
      ip: "Según tu IP.",
    } as Record<"timezone" | "language" | "ip", string>,
    locateUncovered: (country: string) =>
      `Parece que estás en ${country}, pero aún no seguimos precios allí. Elige el país contra el que quieres comparar.`,
    locateLoading: "Consultando tu país por IP…",
    locateFailed: "No pudimos consultar tu IP. Elige tu país en la lista.",
    locateIp: "Usar mi IP",
    ownLabel: (country: string) => `2. ¿Qué tienes de ${country}?`,
    ownDoc: "Documento de identidad o permiso de residencia",
    tiesLabel: "3. ¿Tienes vínculos con otros países?",
    tiesHelp:
      "Tu nacionalidad u otros países donde tienes documentos, tarjeta, teléfono o dirección, o adonde viajas. Solo listamos países cuyos precios seguimos.",
    tiesNone: "Ninguno.",
    tieCountry: (n: number) => `País del vínculo ${n}`,
    tieAdd: "Añadir un país…",
    countryNoMatch: "Ningún país coincide.",
    countryMatches: (n: number) =>
      n === 0 ? "Ningún país coincide." : n === 1 ? "1 país coincide." : `${n} países coinciden.`,
    tieRemove: (country: string) => `Quitar ${country}`,
    tieRemoveText: "Quitar",
    tieDoc: "Documento de identidad o pasaporte",
    tieCard: "Tarjeta emitida allí",
    tiePhone: "Número de teléfono",
    tieAddress: "Dirección real",
    tiePresence: (country: string) => `¿Viajas a ${country}?`,
    tiePresenceHelp:
      "Ir con regularidad desbloquea el nivel 2: muchas ofertas piden volver al país cada cierto tiempo.",
    tieNoCard: (country: string) =>
      `Sin una tarjeta de ${country}, casi ningún servicio de allí se puede contratar.`,
    presence: { none: "No viajo allí", once: "De vez en cuando", periodic: "Con regularidad" },
    adviceTitle: (country: string) => `Qué te abre vivir en ${country}`,
    adviceLead:
      "Cuentas que puedes abrir por internet con tu residencia y tus documentos. Pueden darte una tarjeta de otro país, que algunos servicios aceptan; no cambian dónde vives ni las condiciones de ningún servicio. Las etiquetas de arriba ya cuentan con ellas.",
    adviceNone: "No tenemos registrada ninguna cuenta que se pueda abrir online con este perfil.",
    adviceIssuer: (country: string) => `Entidad de ${country}`,
    adviceCard: (country: string) => `tarjeta emitida en ${country}`,
    adviceCardUnknown: "país de la tarjeta sin confirmar",
    adviceEea:
      "Al vivir en la UE/EEE, tus suscripciones de vídeo, música y juegos siguen funcionando cuando viajas temporalmente a otro país de la UE (Reglamento (UE) 2017/1128 de portabilidad).",
    adviceHonest:
      "Datos curados a mano y sin verificar en tiempo real: comprueba los requisitos en la web de cada entidad. Nunca declares una residencia que no tienes.",
    pendingNote:
      "Algunas ofertas usan hallazgos pendientes de revisión: todavía nadie los ha comprobado.",
    loading: "Cargando precios…",
    noscript:
      "El comparador necesita JavaScript. Sin él puedes consultar los precios por servicio o por país.",
    noneSelected: "Elige al menos un servicio para ver la comparación.",
    resultsTitle: "Tu resultado",
    savingYearLabel: "Ahorro neto al año",
    savingMonthLabel: (amount: string) => `${amount} al mes`,
    feeChip: (fee: string) => `Comisión de cambio del ${fee} incluida`,
    totalOptimized: "Total mensual optimizado",
    serviceSaving: (amount: string) => `${amount} / mes`,
    shareButton: "Compartir mi comparación",
    shareCopied: "Enlace copiado",
    shareFailed: "Copia la dirección de la página para compartirla",
    detailTitle: "Detalle por servicio",
    rowTotals: "Totales",
    perYear: (amount: string) => `${amount} al año`,
    savingNone: "Con este riesgo, tu país ya es la opción más barata.",
    savingNoneEvaluated:
      "Para tu perfil aún no hay ofertas de otros países con el riesgo evaluado, así que no sumamos ningún ahorro.",
    savingUnevaluated: (amount: string) =>
      `Las ofertas sin evaluar de otros países costarían hasta ${amount} al mes menos. No se suman: míralas abajo.`,
    savingMore: (amount: string) =>
      `Aceptando más riesgo podrías ahorrar hasta ${amount} al mes. Compáralo abajo.`,
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
    homeIsBestEvaluated: "Tu país es la opción más barata entre las evaluadas",
    unevaluatedOffer: (country: string, price: string, diff: string | null) =>
      `Más barata pero sin evaluar para tu perfil: ${country}, ${price} al mes${diff ? ` (${diff})` : ""}`,
    runnerUp: (country: string, price: string, diff: string) =>
      `Le sigue ${country}: ${price} al mes (${diff})`,
    runnerUpAboveRisk: (country: string, price: string, diff: string) =>
      `La más barata de otro país es ${country}, con más riesgo del aceptado: ${price} al mes (${diff})`,
    noRunnerUp: "No hay ofertas viables de otros países",
    localPrice: (price: string) => `Precio local: ${price}`,
    cardViaShort: (name: string) => `con tarjeta ${name}`,
    cardViaBefore: "Pagas con una tarjeta ",
    cardViaAfter: (country: string) =>
      `, emitida en ${country}, que abres en línea. Sin ella no hay ahorro.`,
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
    savingsByRiskTitle: "Ahorro según el riesgo aceptado",
    resultRowsLead: (residence: string) =>
      `Para cada servicio elegimos el país más barato que puedes contratar con este riesgo; si ninguno mejora el precio, queda ${residence}.`,
    savingsByRiskHelp:
      "Con la misma selección, qué parte de lo que pagas hoy ahorrarías en cada nivel. Pulsa o arrastra para cambiar el nivel.",
    riskSliderSaving: (pct: string) => `ahorro del ${pct}`,
    /** Level 0 is the official price where you live, so it reads as what you already pay. */
    riskStop: (rank: number) => (rank === 0 ? "Lo que pagas hoy" : `Nivel ${rank}`),
    /** The same stop in the narrow slider pill. */
    riskStopShort: (rank: number) => (rank === 0 ? "Hoy" : `Nivel ${rank}`),
    /** Shown under "Hoy" instead of a saving, which is always 0 there. */
    riskStopTag: "Base",
    fxNote: (date: string) => `Conversión con los tipos de cambio del ${date}.`,
    shareNote: "La selección se guarda en la dirección de la página, así puedes compartirla.",
    servicePageLink: (name: string) => `Ver todos los precios de ${name}`,
    summaryLive: (saving: string, yearly: string) =>
      `Ahorro neto estimado: ${saving} al mes, ${yearly} al año.`,
  },

  visitorProfile: {
    /** Lead of the note on pages that follow the visitor's profile. */
    forYou: (residence: string, ties: string | null) =>
      `Precios y riesgos para tu perfil: vives en ${residence}${
        ties === null ? ", sin vínculos con otros países" : `, con vínculos con ${ties}`
      }.`,
    forDefault: (profile: string) => `Precios y riesgos del perfil «${profile}».`,
    change: "Cámbialo en el comparador",
    feasibilityLead:
      "Etiquetas para tu perfil, con las mismas reglas que el comparador. Solo aparecen los países que tenemos evaluados; las fichas con pruebas y notas solo existen para los perfiles revisados a mano.",
    countryFeasibilityLead:
      "Etiquetas para tu perfil, con las mismas reglas que el comparador. Solo aparecen los servicios que tenemos evaluados en este país.",
    unevaluated: (count: number) =>
      count === 1 ? "1 país más sin evaluar para tu perfil." : `${count} países más sin evaluar para tu perfil.`,
    unevaluatedServices: (count: number) =>
      count === 1
        ? "1 servicio más sin evaluar para tu perfil."
        : `${count} servicios más sin evaluar para tu perfil.`,
    noneEvaluated: "Ninguna oferta de otro país está evaluada todavía para tu perfil.",
    ownCountry: "Es tu país de residencia: sus ofertas son la oferta oficial de tu país.",
    bundleCaption: (label: string) => `Paquetes con el veredicto «${label}» para tu perfil`,
  },

  servicesIndex: {
    title: "Servicios",
    heading: "Tus servicios, sin fronteras",
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
    noBundles: "No hay paquetes vigentes que incluyan este servicio.",
    discountBundlesTitle: "Paquetes con descuento en este servicio",
    discountBundlesLead:
      "No lo incluyen: dan un descuento o meses gratis si además lo contratas, y ese ahorro no cuenta en el ahorro del paquete.",
    feasibilityTitle: (residence: string) => `Factibilidad desde ${residence}`,
    feasibilityLead: (residence: string, profile?: string) =>
      `Fichas para quien vive en ${residence}${profile ? ` (perfil «${profile}»)` : ""}. Las curadas a mano son semillas conservadoras, casi todas sin verificar; las del agente de investigación citan sus fuentes y, hasta que una persona las revisa, figuran como «Pendiente de revisión»; el comparador ya las tiene en cuenta.`,
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

  bundlesIndex: {
    title: "Paquetes",
    heading: "Paquetes, pieza a pieza",
    lead: "Paquetes promocionales que reúnen varias suscripciones por un solo precio, agrupados por su veredicto para tu perfil y, dentro de cada grupo, del mayor al menor ahorro.",
    profileNote: (profile: string) =>
      `Veredicto del perfil por defecto: ${profile}. Al lado, el de los demás perfiles.`,
    empty: "Todavía no hay paquetes vigentes registrados.",
    groupCount: (count: number) => `${count} ${count === 1 ? "paquete" : "paquetes"}`,
    caption: (label: string) => `Paquetes con el veredicto «${label}» para el perfil por defecto`,
    colBundle: "Paquete",
    colCountry: "País",
    colStatus: "Estado",
    colMonthly: "Mensual (EUR)",
    colSaving: "Ahorro",
    colOthers: "Otros perfiles",
    marginal: (full: string) => `coste adicional al plan base; completo: ${full}`,
    pendingVerdict: "Con hallazgos pendientes:",
    noOthers: "Sin otros perfiles",
    cardLink: (name: string, country: string) =>
      `${name}: ficha completa en la página de ${country}`,
    savingNote:
      "El ahorro compara el precio regular con los servicios incluidos comprados por separado en el país del paquete. No cuenta la comisión de cambio, los precios de introducción, los descuentos ni los meses gratis.",
    methodologyLink: "Cómo se verifican los paquetes",
  },

  countriesIndex: {
    title: "Países",
    heading: "Un servicio. Distintos precios",
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
    noBundles: "No hay paquetes vigentes registrados en este país.",
    feasibilityTitle: (residence: string) => `Factibilidad desde ${residence}`,
    noFeasibility: "No hay fichas de factibilidad para este país.",
    backToCountries: "Todos los países",
  },

  changes: {
    title: "Cambios de precio",
    heading: "Cada cambio, a la vista",
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
    heading: "Sin magia. Con método",
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
      "En las fichas curadas a mano, si algún paso exige declarar datos falsos (por ejemplo, una dirección de otro país), la etiqueta es como mínimo de nivel 2 y el eje de legalidad lo indica; en las fichas derivadas por el agente de investigación, esa vía es «No factible».",
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
        text: "Los hallazgos nuevos figuran como «Pendiente de revisión» hasta que una persona los acepta. Mientras tanto, el comparador ya los tiene en cuenta y marca las ofertas que dependen de ellos; en las fichas se ven con su distintivo y sus citas.",
      },
      {
        title: "Nunca datos falsos",
        text: "En las fichas derivadas por el agente, una vía que exigiría declarar algo falso, como una dirección o un país que no son los tuyos, se muestra como «No factible». Las fichas curadas a mano marcan ese paso y su eje de legalidad dice «Requiere declarar datos falsos». El sitio nunca sugiere usar una VPN ni dar datos falsos.",
      },
    ],
    researchRun: (date: string, run: string | null) =>
      run
        ? `Última investigación publicada: ${date} (ejecución ${run}).`
        : "Todavía no se ha publicado ninguna investigación: solo hay fichas escritas a mano.",
    profilesTitle: "Perfiles",
    profilesLead: (byIp: boolean) =>
      `Cada perfil describe lo que una persona puede demostrar honestamente: residencia, documentos, teléfono, dirección, medios de pago y viajes. Todas las páginas (comparador, servicios, países, paquetes) muestran precios y riesgos para tu perfil. Al llegar empezamos por tu país, sin vínculos: ${
        byIp
          ? "tu navegador pregunta a country.is el país de tu dirección IP (country.is recibe la IP; no guardamos nada); si no responde, lo deducimos de la zona horaria o el idioma de tu navegador"
          : "lo deducimos de la zona horaria o el idioma de tu navegador sin enviar nada; si pulsas «Usar mi IP», tu navegador consulta a country.is, que recibe tu dirección IP"
      }. Luego describes tu perfil en el comparador (país de residencia entre los que tienen precios, lo que tienes de él y vínculos con cualquier otro país); lo guardamos solo en tu navegador (almacenamiento local, nunca se envía ni es una cookie) para que las demás páginas lo usen. Sin JavaScript, las páginas muestran el perfil de ejemplo de abajo. El comparador también muestra las cuentas que podrías abrir por internet con esa residencia y esos documentos, según nuestros datos curados. Para un perfil propio, las etiquetas salen de aplicar las mismas reglas a los hechos investigados; las fichas curadas a mano solo valen para el perfil para el que se escribieron.`,
    defaultProfileTag: "Por defecto",
    researchNotLegal:
      "No es asesoría legal: describe lo que encontramos en fuentes públicas, no lo que debas hacer.",
    bundlesTitle: "Paquetes",
    bundlesLead:
      "Un paquete reúne varias suscripciones por un solo precio, a menudo con un plan de telefonía o una suscripción de tienda. Sus precios, servicios y condiciones cambian sin aviso, así que se publican con más cautela que los precios diarios.",
    bundles: (fee: string) => [
      {
        title: "Una sola fuente para los valores",
        text: "Los valores de cada paquete (precio, precio de introducción, plan base, permanencia, servicios incluidos, fechas y requisitos) salen solo de un archivo curado a mano. Ningún proceso automático lo modifica: lo automático solo puede aportar pruebas sobre esos valores o proponer cambios, y una persona los revisa.",
      },
      {
        title: "Qué cuenta como confirmado",
        text: "Un valor solo figura como confirmado si nuestra propia descarga encontró su cita literal, el «ancla», en la página oficial o en el PDF oficial del país del paquete hace 15 días o menos. Si la fuente es una lista de precios en PDF con ediciones, solo cuenta la edición vigente. Una página bloqueada, ilegible o de otro país no confirma nada. Si falta algo, el valor se muestra «sin confirmar» y el paquete dice por qué.",
      },
      {
        title: "Verificación manual",
        text: "Una persona puede comprobar el paquete a mano; queda registrada la fecha de esa comprobación. Se muestra como «Verificación manual», con los días transcurridos, caduca a los 45 días y nunca cuenta como fuente oficial.",
      },
      {
        title: "Ahorro sin adornos",
        text: "El ahorro compara el precio regular del paquete con la suma de los servicios incluidos comprados por separado en el mismo país, cada uno a su precio más barato, así que nunca se exagera. No cuenta precios de introducción, descuentos ni meses gratis. Los descuentos y los meses gratis se muestran aparte; un precio de introducción, solo cuando el paquete está verificado. Cuando el paquete exige un plan base, como una línea móvil, el ahorro se calcula sobre el precio marginal, es decir, el precio del paquete menos el del plan base, y la ficha lo indica.",
      },
      {
        title: "Comisión de cambio",
        text: `Si el paquete se paga en una moneda distinta de la de tu país, la ficha muestra también el precio con la comisión de cambio de la tarjeta (${fee}). El porcentaje de ahorro no la incluye, porque compara precios del mismo país.`,
      },
      {
        title: "Un veredicto por perfil",
        text: "Cada perfil público recibe su veredicto, con los mismos ejes y etiquetas que las fichas de factibilidad. Si hay una ficha curada a mano para el paquete y el perfil, se parte de ella, y los requisitos y los servicios incluidos solo pueden empeorarla; se muestra siempre sin verificar y con confianza baja, porque ninguna cita de los términos la confirma en nuestra lectura de las páginas. Si el paquete es del país de residencia, es la oferta oficial de tu país, salvo que exija algo que el perfil no tiene. Si no, el veredicto se compone de los requisitos del paquete y del veredicto, en ese país, de cada servicio incluido; si su precio es el de un servicio que ya seguimos, como Apple One, cuenta el veredicto de ese servicio. Para los demás paquetes también cuentan sus propios términos: si no encontramos en ellos una regla sobre el país de residencia, el eje «Términos del servicio» queda «Sin datos», y que permitan contratarlo desde otro país solo cuenta con una cita oficial. Manda el peor, y el nivel del paquete nunca es menor que el de sus servicios incluidos.",
      },
      {
        title: "Los requisitos se enuncian como hechos",
        text: "Si el paquete exige algo que el perfil no tiene, como un documento de identidad, una línea de teléfono o una tarjeta del país, el veredicto lo dice tal cual y esa vía es «No factible». Cuando el veredicto se compone de los propios términos y requisitos del paquete, un requisito sin datos figura como supuesto y nunca mejora el veredicto. Un requisito que los términos del paquete indican cuenta en cuanto una persona revisa ese hallazgo, y uno que facilita la contratación solo con una cita oficial; un requisito de los datos curados nunca se relaja. Si parte de una ficha curada, de la oferta de tu país o del veredicto de un servicio que ya seguimos, solo cuentan los requisitos conocidos.",
      },
      {
        title: "Nunca datos falsos",
        text: "En los veredictos compuestos, una vía que exigiría declarar algo que no es cierto, como una residencia o una dirección de facturación que no son las tuyas, es «No factible». En una ficha curada a mano, ese paso queda marcado, el eje de legalidad lo indica y el nivel es como mínimo 2. Si la ficha curada de un servicio incluido tiene un paso así, el paquete hereda ese paso, su razón y, como mínimo, su nivel. El sitio nunca sugiere usar una VPN, otra dirección ni datos falsos.",
      },
    ],
    bundlesNotLegal:
      "No es asesoría legal: los veredictos describen lo que encontramos en fuentes públicas y lo que cada perfil puede demostrar honestamente, no lo que debas hacer.",
    watchedTitle: "Paquetes vigilados",
    watchedLead:
      "Todos los paquetes publicados, también los que no se vigilan: su fuente oficial, cómo se comprueban, su estado, su última comprobación automática y cuántas de sus anclas se han verificado. Una raya (—) indica que todavía no hay ninguna.",
    watchedCaption: "Paquetes vigilados: fuente, vigilancia, estado y anclas verificadas",
    watchedEmpty: "No hay paquetes publicados.",
    colBundle: "Paquete",
    colSource: "Fuente oficial",
    colWatch: "Vigilancia",
    colLastCheck: "Última comprobación",
    colAnchors: "Anclas verificadas",
    anchors: (verified: number, total: number) => `${verified} de ${total}`,
    anchorsNone: "Todavía sin anclas",
    pdfTag: "PDF",
    pdfTitle: "Documento PDF oficial",
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
  /** Spanish names come from the data (feasibility.json profiles[].name_es). */
  profileNames: {} as Record<string, (residence: string) => string>,
  /** Spanish plan names come from the data (services.json plans[].label_es). */
  planNames: {} as Record<string, string>,

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
} satisfies Messages;
