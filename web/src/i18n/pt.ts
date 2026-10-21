/**
 * Portuguese (Brazilian) copy. Same shape as en.ts (the default locale), which the type below
 * enforces.
 */

import type { Messages } from "./en";

export const pt = {
  site: {
    name: "Haggler",
    tagline: "Preços de assinaturas por país, com o seu risco",
    description: (residence: string) =>
      `Compare os preços oficiais de Netflix, Spotify, iCloud+ e outros em cada país, com o risco de cada opção se você mora em ${residence}.`,
    /** Social preview card (public/og/haggler.png, drawn by scripts/brand-assets.mjs). */
    ogImageAlt: "Haggler: preços de assinaturas por país, com o seu risco.",
    seo: {
      serviceTitle: (name: string) => `${name}: preço em cada país`,
      serviceDescription: (name: string, countries: number) =>
        `Preço oficial de ${name} em ${countries} ${countries === 1 ? "país" : "países"}, convertido para a sua moeda, com o risco de assinar de fora do seu país de residência.`,
      countryTitle: (name: string) => `Preços de assinaturas em ${name}`,
      countryDescription: (name: string, currency: string) =>
        `Preços oficiais de Netflix, Spotify, iCloud+ e outras assinaturas em ${name} (${currency}), pacotes locais e o risco de assiná-los de fora.`,
    },
    skipToContent: "Pular para o conteúdo",
    navLabel: "Principal",
    nav: {
      home: "Comparador",
      services: "Serviços",
      countries: "Países",
      bundles: "Pacotes",
      changes: "Mudanças",
      methodology: "Metodologia",
    },
    sampleBanner:
      "Dados de exemplo: estes preços são ilustrativos e não vêm de uma execução real do agente.",
    footer: {
      disclaimer:
        "Informação sobre preços e riscos, não assessoria jurídica. Nunca recomendamos declarar dados falsos.",
      dataAsOf: (date: string, run: string | null) =>
        run ? `Dados de ${date} · execução ${run}` : `Dados de ${date} · ainda sem execuções`,
      generatedAt: (when: string) => `Gerado em ${when}`,
      methodologyLink: "Como os dados são obtidos",
      noAnalytics: "Sem analytics nem cookies.",
    },
    pageEyebrow: (data: string) => `Haggler · ${data}`,
    sampleData: "Dados de exemplo",
    dataAsOfShort: (date: string) => `Dados de ${date}`,
    backToCompare: "Voltar ao comparador",
  },

  language: {
    menuLabel: "Idioma",
    switchTo: "Mudar para português",
    spanishOnly: "Em espanhol",
  },

  common: {
    yes: "Sim",
    no: "Não",
    unknown: "Desconhecido",
    officialSite: "Site oficial",
    source: "Fonte",
    open: "Abrir",
    sourceLinkLabel: (kind: string, what: string) => `Fonte ${kind.toLowerCase()}: ${what}`,
    externalLink: "(link externo)",
    residenceTag: "Seu país",
    staleFx: "Câmbio desatualizado",
    staleFxNote: "* Câmbio desatualizado: foi usada a última tabela disponível.",
    perMonth: "/mês",
    tableScrollHint: "Deslize a tabela para o lado para ver todas as colunas.",
    countryAndName: (flag: string, name: string) => `${flag} ${name}`.trim(),
  },

  periods: { month: "/mês", quarter: "/trimestre", year: "/ano" } as Record<string, string>,
  periodNames: { month: "Mensal", quarter: "Trimestral", year: "Anual" } as Record<string, string>,

  channels: { web: "Web", ios_iap: "App Store" } as Record<string, string>,
  channelTitles: {
    web: "Preço publicado no site oficial do serviço",
    ios_iap:
      "Preço da compra dentro do app na App Store; pode incluir um acréscimo em relação ao site",
  } as Record<string, string>,

  sourceKinds: { official: "Oficial", secondary: "Secundária" } as Record<string, string>,

  extraction: {
    parser: "Parser",
    llm: "LLM (citação literal)",
  } as Record<string, string>,

  offerStatus: {
    accepted: "Aceito",
    quarantined: "Em revisão",
    rejected: "Rejeitado",
    carried_forward: "Valor anterior",
  } as Record<string, string>,
  offerStatusTitles: {
    quarantined: "Mudança grande aguardando confirmação; não é usada no comparador",
    rejected: "Descartado pela validação; não é usado no comparador",
    carried_forward: "A fonte falhou hoje; mantém-se o último valor confirmado",
  } as Record<string, string>,

  flags: {
    iap_maybe_marked_up: "App Store: pode ter acréscimo",
    apple_billing: "Cobrança pela Apple",
    secondary_disagrees: "A fonte secundária diverge",
    iap_below_web_stale: "App Store abaixo do site (possível defasagem)",
    iap_duplicate: "Compra no app duplicada",
    taxes_not_included: "Impostos não incluídos",
    quarantine_hold: "Mudança em revisão: mostra-se o valor anterior",
  } as Record<string, string>,

  categories: {
    video: "Vídeo",
    music: "Música",
    cloud: "Armazenamento em nuvem",
    bundle: "Pacote",
    productivity: "Produtividade",
    gaming: "Jogos",
    ai: "Inteligência artificial",
    other: "Outros",
  } as Record<string, string>,

  regions: {
    nordics: "Países nórdicos",
    eu: "União Europeia",
    europe_other: "Resto da Europa",
    latam: "América Latina",
    north_america: "América do Norte",
    asia: "Ásia",
    africa: "África",
    mena: "Oriente Médio e Norte da África",
  } as Record<string, string>,

  euGeoClass: {
    audiovisual_excluded: "Audiovisual: excluído do Regulamento de bloqueio geográfico",
    copyright_art3_5_only: "Obras protegidas: só os arts. 3.º e 5.º do Regulamento",
    eservice_art4_1b: "Serviço eletrônico tipo nuvem (art. 4.º, n.º 1, b): direito da UE não testado",
    software_unclear: "Software: enquadramento incerto no Regulamento",
  } as Record<string, string>,

  /** Used when feasibility.json does not carry a label (or is older than the site). */
  riskLabels: {
    official_home: {
      name: "Oficial",
      description: "Preço do país onde você mora. Sem conflito com os termos do serviço.",
    },
    eu_right_untested: {
      name: "Direito da UE não testado",
      description:
        "Cumpre os termos, mas depende de uma interpretação do Regulamento (UE) 2018/302 que nenhum tribunal confirmou.",
    },
    tos_breach_low_enforcement: {
      name: "Viola os termos, sem controle conhecido",
      description:
        "Contraria os termos do serviço; não se conhecem cancelamentos nem bloqueios por isso.",
    },
    tos_breach_enforced: {
      name: "Viola os termos, com controle ativo",
      description: "O serviço verifica o país e cancela ou bloqueia contas que não cumprem.",
    },
    not_feasible: {
      name: "Não viável",
      description: "Não dá para conseguir de forma realista com o seu perfil.",
    },
    unknown: {
      name: "Sem avaliação",
      description: "Ainda não há análise de viabilidade. Nunca é escolhida como opção.",
    },
  } as Record<string, { name: string; description: string }>,
  riskLevel: (rank: number) => `Nível ${rank}`,
  euBadge: "UE",
  euBadgeTitle: (residence: string) =>
    `Poderia existir um direito pelo Regulamento (UE) 2018/302 (não testado; preço líquido + IVA de ${residence})`,

  axes: {
    legality: {
      name: "Legalidade",
      question: () => "Vai contra alguma norma?",
      values: {
        lawful: "Legal",
        contestable_eu_right: "Direito da UE discutível",
        requires_misrepresentation: "Exige declarar dados falsos",
        unknown: "Sem dados",
      },
    },
    tos: {
      name: "Termos do serviço",
      question: () => "Os termos do serviço permitem, e isso é controlado?",
      values: {
        compliant: "Cumpre",
        breach_unenforced: "Viola, sem controle conhecido",
        breach_enforced: "Viola, com controle ativo",
        unknown: "Sem dados",
      },
    },
    practical: {
      name: "Praticidade",
      question: (residence: string) => `Dá para fazer de ${residence}?`,
      values: {
        remote: "A distância",
        presence_once: "Presença no país uma vez",
        presence_periodic: "Presença periódica",
        impossible: "Impossível",
        unknown: "Sem dados",
      },
    },
  } as Record<
    string,
    { name: string; question: (residence: string) => string; values: Record<string, string> }
  >,

  confidence: { low: "Baixa", medium: "Média", high: "Alta" } as Record<string, string>,
  curatedStatus: {
    verified: "Verificado",
    unverified: "Não verificado",
    expired: "Expirado",
  } as Record<string, string>,

  /** Where a feasibility record comes from (feasibility.json records[].provenance). */
  provenance: {
    home: "Seu país",
    curated: "Curado",
    composed: "Por requisitos",
    template: "Por analogia",
    agent_reviewed: "Revisado",
    agent_pending: "Revisão pendente",
  } as Record<string, string>,
  provenanceTitles: {
    home: "Oferta do país onde você mora, avaliada com os requisitos que exige.",
    curated: "Ficha escrita à mão; sempre prevalece sobre a pesquisa automática.",
    composed:
      "Calculado a partir dos requisitos do pacote e do veredito de cada serviço incluído.",
    template:
      "Mesmos termos e passos da ficha curada do serviço, aplicados a este país sem pesquisá-lo especificamente.",
    agent_reviewed:
      "Calculada a partir de achados do agente de pesquisa que uma pessoa aceitou.",
    agent_pending:
      "Calculada com achados do agente que ninguém revisou ainda. O comparador a usa, marcada como pendente, até que uma pessoa a revise.",
  } as Record<string, string>,
  /** Source class of a cited page (records[].evidence[].kind). */
  evidenceKinds: {
    official: "oficial",
    press: "imprensa",
    forum: "fórum",
    aggregator: "agregador",
    user_report: "relato de usuário",
  } as Record<string, string>,

  /** The evidence block of a feasibility record (FeasibilityTable). */
  feasibility: {
    colSource: "Fonte",
    researchedOn: (date: string) => `Dados de ${date}`,
    evidenceSummary: (count: number) => `Evidências (${count})`,
    noEvidence: "Sem citações.",
    sourceKind: (kind: string) => `fonte ${kind}`,
    retrievedOn: (date: string) => `consultado em ${date}`,
    verifiedQuote: "citação verificada",
    unverifiedQuote: "citação não verificada",
    evidenceLinkLabel: (host: string, kind: string) => `${host} (fonte ${kind})`,
    assumptionsTitle: "Pressupostos",
    reasonsTitle: "Motivos",
  },

  coverageStatus: {
    ok: "OK",
    partial: "Parcial",
    failed: "Falhou",
    not_available: "Não disponível",
    blocked: "Bloqueado",
    robots_disallowed: "Proibido pelo robots.txt",
    not_configured: "Não configurado",
    wrong_country: "País errado",
  } as Record<string, string>,

  changeKinds: {
    new: "Novo",
    added: "Novo",
    changed: "Mudança de preço",
    price_change: "Mudança de preço",
    up: "Alta",
    increase: "Alta",
    down: "Queda",
    decrease: "Queda",
    removed: "Retirado",
    dropped: "Retirado",
    status: "Mudança de status",
  } as Record<string, string>,

  bundle: {
    price: "Preço",
    monthlyEur: "Mensal (EUR)",
    fullMonthlyEur: "Completo (EUR)",
    marginalEur: "Adicional (EUR)",
    standalone: "Separadamente",
    saving: "Economia",
    /** The line under the full price of a bundle sold on top of a base plan. */
    marginalLine: (basePlan: string, amount: string) => `Se você já paga ${basePlan}: +${amount}`,
    basePlanFallback: "o plano base",
    basePlanWithPrice: (name: string, price: string) => `${name} (${price})`,
    observedOn: (service: string, date: string) => `Preço de ${service} publicado em ${date}`,
    observedMissing: (service: string) => `Sem preço de ${service} na última execução.`,
    introLine: (amount: string, months: number) =>
      `Preço promocional: ${amount} durante ${months} ${months === 1 ? "mês" : "meses"}`,
    introUntil: (date: string) => `para adesões até ${date}`,
    firstYear: "Primeiro ano (EUR)",
    firstYearValue: (amount: string) => `${amount} em média por mês`,
    contract: (months: number) => `Fidelidade: ${months} ${months === 1 ? "mês" : "meses"}`,
    withFee: (amount: string, fee: string) => `${amount} incl. taxa de câmbio de ${fee}`,
    partialSum: (names: string) => `soma parcial: sem preço avulso de ${names}`,
    noStandalone: (names: string) => `Sem preço avulso de ${names}.`,
    unverifiedSaving: "não verificada",
    marginalSaving: "sobre o custo adicional",
    savingHelp:
      "Em comparação com comprar separadamente os serviços incluídos, no país do pacote. Não conta a taxa de câmbio, os preços promocionais, os descontos nem os meses grátis.",
    optionalTitle: "Se você também assinar",
    optionalHelp: "Descontos e meses grátis em outros serviços; não entram na economia.",
    optionalSaving: (amount: string) => `≈ −${amount}/mês`,
    optionalNoPrice: "sem preço de referência",
    freeMonthsNote: "não é distribuído como economia mensal",
    components: "Inclui",
    standaloneEach: (amount: string) => `${amount}/mês separadamente`,
    confirmed: "confirmado",
    unconfirmed: "não confirmado",
    confirmedTitle: "Encontramos isso em uma página oficial do país do pacote.",
    unconfirmedTitle: "Ainda não encontramos isso em uma página oficial do país do pacote.",
    requirements: "Requisitos",
    lastVerified: (date: string) => `Última revisão: ${date}`,
    noPrice: "Preço não confirmado",
    officialPage: "Página oficial",
    inclusion: {
      included: () => "Incluído",
      discount_pct: (v: string) => `${v} de desconto`,
      discount_abs: (v: string) => `${v} de desconto`,
      free_months: (v: string) => (v === "1" ? "1 mês grátis" : `${v} meses grátis`),
      choose_n: (n: string) => (n ? `À escolha (${n} do grupo)` : "À escolha"),
    } as Record<string, (value: string) => string>,
    withAds: "com anúncios",
    taxIncluded: "Impostos incluídos",
    taxExcluded: "Impostos não incluídos",
    /** display_status: derived from the watcher, a manual check and the dates, never typed. */
    displayStatus: {
      verified: "Verificado",
      needs_review: "Precisa de revisão",
      manual: "Verificação manual",
      unverified: "Não verificado",
      expired: "Expirada",
    } as Record<string, string>,
    displayStatusTitles: {
      verified:
        "Preço e componentes encontrados há 15 dias ou menos em uma página oficial do país do pacote.",
      needs_review:
        "A página oficial já não mostra algum valor registrado, mostra um diferente ou publicou um documento novo: falta revisá-lo.",
      manual:
        "Uma pessoa conferiu à mão há 45 dias ou menos. Não conta como fonte oficial.",
      unverified: "Nenhum valor está confirmado em uma página oficial.",
      expired: "A oferta terminou; aparece só como referência.",
    } as Record<string, string>,
    verifiedOn: (date: string) => `conferido em ${date}`,
    manualAgo: (days: number) =>
      days === 0 ? "hoje" : `há ${days} ${days === 1 ? "dia" : "dias"}`,
    checkedOn: (date: string) => `última verificação: ${date}`,
    expiredOn: (date: string) => `terminou em ${date}`,
    validFrom: (date: string) => `Disponível desde ${date}`,
    validUntil: (date: string) => `Disponível até ${date}`,
    statusReasons: "Por quê",
    forProfile: "Para o seu perfil",
    otherProfiles: (count: number) => `Outros perfis (${count})`,
    pendingVariant: "Com achados pendentes de revisão",
    noVerdict: "Sem veredito para este perfil.",
    expiredNoVerdict: "Oferta expirada: não é avaliada.",
    verdictStatus: "Status da ficha",
    verdictConfidence: "Confiança",
    perComponent: "Por serviço incluído",
    evidenceSummary: (count: number) => `Fontes (${count})`,
    evidenceFacts: {
      price: "preço",
      base_price: "plano base",
      intro: "preço promocional",
      component: "serviço incluído",
      validity: "vigência",
    } as Record<string, string>,
    evidenceVia: {
      embedded: "dado embutido",
      embedded_only: "dado embutido",
      text: "texto visível",
      visible: "texto visível",
      pdf: "documento PDF",
    } as Record<string, string>,
    evidenceEdition: (edition: string) => `edição ${edition}`,
    evidenceStatus: {
      verified: "encontrada",
      missing: "não encontrada",
      unreadable: "página ilegível",
      embedded_only: "só em dados embutidos",
      outdated_document: "documento desatualizado",
      not_checked: "não conferida",
    } as Record<string, string>,
    /** pending_changes: official values that differ from the curated data, awaiting review. */
    pendingTitle: (count: number) => `Mudanças pendentes de revisão (${count})`,
    pendingLead:
      "Valores que uma página oficial do país do pacote indica e os dados curados ainda não. Não mudam nada até que uma pessoa os revise e edite os dados.",
    pendingFields: {
      bundle_price: "Preço",
      bundle_intro_price: "Preço promocional",
      bundle_base_price: "Preço do plano base",
      bundle_intro_months: "Meses do preço promocional",
      bundle_contract_months: "Fidelidade",
      bundle_intro_until: "Fim do preço promocional",
      bundle_offer_until: "Fim da oferta",
      bundle_includes: "Serviço incluído",
      bundle_discount: "Desconto",
      bundle_available: "Continua à venda",
      bundle_price_list_url: "Tabela de preços",
    } as Record<string, string>,
    pendingSources: {
      watcher: "o monitor",
      agent: "a pesquisa automática",
    } as Record<string, string>,
    pendingNone: "sem registro",
    pendingChange: (current: string, proposed: string) => `${current} → ${proposed}`,
    pendingMonths: (months: number) => `${months} ${months === 1 ? "mês" : "meses"}`,
    pendingDiscount: (service: string, pct: string) => `${pct} em ${service}`,
    pendingSeen: (source: string, date: string) => `segundo ${source}, ${date}`,
    pendingBigChange: "mudança grande",
    pendingBigChangeTitle: "Mais de 50 % de diferença em relação ao valor registrado.",
    /** watch_mode: how the bundle's official pages are checked. */
    watchModes: {
      auto: "Automático",
      manual: "Manual",
      off: "Sem monitoramento",
    } as Record<string, string>,
    watchModeTitles: {
      auto: "As páginas oficiais estão no monitoramento automático semanal; se ainda não foram lidas, a ficha indica isso.",
      manual: "As páginas não são lidas automaticamente: uma pessoa as revisa.",
      off: "Os valores não são conferidos automaticamente.",
    } as Record<string, string>,
    expiredTitle: (count: number) => `Ofertas expiradas (${count})`,
    expiredLead: "Pacotes cuja oferta já terminou. Aparecem por um tempo como referência.",
    requirementFields: {
      residency: "Residência no país",
      billing_address: "Endereço de cobrança no país",
      signup_presence: "Presença no país ao assinar",
      local_id: "Documento local",
      local_line: "Linha local",
      local_payment: "Pagamento",
      credit_check: "Análise de crédito",
      new_customers_only: "Só clientes novos",
    } as Record<string, string>,
    requirementValues: {
      unknown: "Desconhecido",
      required: "Exigido",
      not_required: "Não exigido",
      checked: "É verificado",
      not_checked: "Não é verificado",
      none: "Não",
      mobile: "Móvel",
      fixed: "Fixa",
      both: "Móvel e fixa",
      any_card: "Qualquer cartão",
      local_card: "Cartão local",
      local_bank: "Conta bancária local",
      carrier_bill: "Fatura da operadora",
      cedula: "Cédula",
      personnummer: "Personnummer",
    } as Record<string, string>,
  },

  home: {
    title: "Comparador de assinaturas",
    eyebrow: "Preços oficiais, país por país",
    heroTitle: "Pague o preço justo pelas suas assinaturas",
    heroAccent: "o preço justo",
    /** The savings tags under the hero title (islands/HeroSavings.tsx), all at level 3. */
    teasers: {
      label: (residence: string) => `Economia no nível 3 morando em ${residence}`,
      headline: (pct: string, countries: string) => `Economize ${pct} em ${countries}`,
      alone: "Sem vínculos com outros países",
      aloneTag: "sem vínculos",
      tie: (country: string) => `Com documento, cartão e viagens regulares para ${country}`,
      linkTitle: "Ver este cálculo no comparador",
    },
    lead: (residence: string) =>
      `O mesmo serviço. Outro país. Um preço diferente. Compare o que você paga pelas suas assinaturas em ${residence} com o preço oficial em outros países. Mostramos quanto você economizaria, com que risco e quanto a taxa de câmbio leva.`,
    ctaStart: "Calcular minha economia",
    ctaMethod: "Como calculamos",
    stats: {
      services: (n: number) => `${n} serviços`,
      countries: (n: number) => `${n} países`,
      asOf: (date: string) => `Dados de ${date}`,
    },
    workflowTitle: "Como o agente trabalha",
    workflow: [
      { title: "Explora preços", text: "País por país" },
      { title: "Avalia o risco", text: "Sem letras miúdas" },
      { title: "Calcula sua economia", text: "Taxas incluídas" },
    ],
    trust: ["Sem cadastro", "Sem cookies nem analytics", "Nunca pedimos dados falsos"],
    stepsTitle: "Três passos, um minuto",
    stepsLead: "Não precisa se cadastrar. Tudo é calculado no seu navegador.",
    steps: [
      {
        title: "Escolha o que você paga",
        text: "Marque suas assinaturas e o plano de cada uma.",
      },
      {
        title: "Conte sua situação",
        text: "Seu perfil, o risco que você aceita e a moeda em que você pensa.",
      },
      {
        title: "Veja sua economia real",
        text: "A oferta mais barata que combina com você, já com a taxa de câmbio.",
      },
    ],
    compareEyebrow: "Sua comparação",
    compareTitle: "Calcule sua economia passo a passo",
    compareLead: "Cada mudança atualiza o resultado na hora.",
    howTitle: "Sem letras miúdas",
    howLink: "Como ler o resultado",
    howItems: [
      {
        title: "Preços oficiais, sem atalhos",
        text: "O preço de cada país é o que o serviço publica, convertido para a moeda que você escolher.",
      },
      {
        title: "O risco é você quem escolhe",
        text: "Uma oferta só é escolhida se o nível de risco dela for igual ou menor que o que você aceita. Ofertas sem avaliação nunca são escolhidas.",
      },
      {
        title: "A economia é líquida",
        text: "Já desconta a taxa de câmbio por pagar em outra moeda.",
      },
      {
        title: "Seus dados ficam com você",
        text: "Tudo é calculado no seu navegador. Antes de mudar qualquer coisa, leia a ficha do serviço e a metodologia.",
      },
    ],
  },

  compare: {
    servicesLegend: "Suas assinaturas",
    servicesHelp: "Marque os serviços que você paga e escolha o plano.",
    planLabel: (service: string) => `Plano de ${service}`,
    riskLegend: "Risco que você aceita",
    riskHelp:
      "Só são escolhidas ofertas com nível de risco igual ou menor. Ofertas sem avaliação nunca são escolhidas.",
    currencyLegend: "Moeda",
    currencyLabel: "Moeda para mostrar os valores",
    currencyCommon: "Frequentes",
    currencyAll: "Todas as moedas",
    profileLegend: "Seu perfil",
    profileHelp:
      "Os rótulos de risco dependem do que você pode comprovar honestamente: onde mora, documentos, telefone, endereço, meios de pagamento e viagens.",
    profileLabel: "Perfil de viabilidade",
    residenceLabel: "1. Em que país você mora?",
    residenceHelp: (host: string) =>
      `Só listamos países cujos preços acompanhamos. «Usar meu IP» envia seu IP para ${host}, só se você clicar.`,
    locateFound: {
      timezone: "Deduzimos pelo fuso horário do seu navegador.",
      language: "Deduzimos pelo idioma do seu navegador.",
      ip: "Pelo seu IP.",
    } as Record<"timezone" | "language" | "ip", string>,
    locateUncovered: (country: string) =>
      `Parece que você está em ${country}, mas ainda não acompanhamos preços lá. Escolha o país com o qual quer comparar.`,
    locateLoading: "Consultando seu país pelo IP…",
    locateFailed: "Não conseguimos consultar seu IP. Escolha seu país na lista.",
    locateIp: "Usar meu IP",
    ownLabel: (country: string) => `2. O que você tem de ${country}?`,
    ownDoc: "Documento de identidade ou autorização de residência",
    tiesLabel: "3. Você tem vínculos com outros países?",
    tiesHelp:
      "Sua nacionalidade ou outros países onde você tem documentos, cartão, telefone ou endereço, ou para onde viaja. Só listamos países cujos preços acompanhamos.",
    tiesNone: "Nenhum.",
    tieCountry: (n: number) => `País do vínculo ${n}`,
    tieAdd: "Adicionar um país…",
    countryNoMatch: "Nenhum país corresponde.",
    countryMatches: (n: number) =>
      n === 0
        ? "Nenhum país corresponde."
        : n === 1
          ? "1 país corresponde."
          : `${n} países correspondem.`,
    tieRemove: (country: string) => `Remover ${country}`,
    tieRemoveText: "Remover",
    tieDoc: "Documento de identidade ou passaporte",
    tieCard: "Cartão emitido lá",
    tiePhone: "Número de telefone",
    tieAddress: "Endereço real",
    tiePresence: (country: string) => `Você viaja para ${country}?`,
    tiePresenceHelp:
      "Ir com regularidade libera o nível 2: muitas ofertas pedem voltar ao país de tempos em tempos.",
    tieNoCard: (country: string) =>
      `Sem um cartão de ${country}, quase nenhum serviço de lá pode ser assinado.`,
    presence: { none: "Não viajo para lá", once: "De vez em quando", periodic: "Com regularidade" },
    adviceTitle: (country: string) => `O que morar em ${country} libera para você`,
    adviceLead:
      "Contas que você pode abrir pela internet com a sua residência e os seus documentos. Podem lhe dar um cartão de outro país, que alguns serviços aceitam; não mudam onde você mora nem as condições de nenhum serviço. Os rótulos acima já contam com elas.",
    adviceNone: "Não temos registrada nenhuma conta que possa ser aberta online com este perfil.",
    adviceIssuer: (country: string) => `Instituição de ${country}`,
    adviceCard: (country: string) => `cartão emitido em ${country}`,
    adviceCardUnknown: "país do cartão não confirmado",
    adviceEea:
      "Morando na UE/EEE, suas assinaturas de vídeo, música e jogos continuam funcionando quando você viaja temporariamente para outro país da UE (Regulamento (UE) 2017/1128 de portabilidade).",
    adviceHonest:
      "Dados curados à mão e não verificados em tempo real: confira os requisitos no site de cada instituição. Nunca declare uma residência que você não tem.",
    pendingNote:
      "Algumas ofertas usam achados pendentes de revisão: ninguém os conferiu ainda.",
    loading: "Carregando preços…",
    noscript:
      "O comparador precisa de JavaScript. Sem ele você pode consultar os preços por serviço ou por país.",
    noneSelected: "Escolha pelo menos um serviço para ver a comparação.",
    resultsTitle: "Seu resultado",
    savingYearLabel: "Economia líquida por ano",
    savingMonthLabel: (amount: string) => `${amount} por mês`,
    feeChip: (fee: string) => `Taxa de câmbio de ${fee} incluída`,
    totalOptimized: "Total mensal otimizado",
    serviceSaving: (amount: string) => `${amount} / mês`,
    shareButton: "Compartilhar minha comparação",
    shareCopied: "Link copiado",
    shareFailed: "Copie o endereço da página para compartilhá-la",
    detailTitle: "Detalhe por serviço",
    rowTotals: "Totais",
    perYear: (amount: string) => `${amount} por ano`,
    savingNone: "Com este risco, seu país já é a opção mais barata.",
    savingNoneEvaluated:
      "Para o seu perfil ainda não há ofertas de outros países com risco avaliado, então não somamos nenhuma economia.",
    savingUnevaluated: (amount: string) =>
      `As ofertas sem avaliação de outros países custariam até ${amount} por mês a menos. Não são somadas: veja-as abaixo.`,
    savingMore: (amount: string) =>
      `Aceitando mais risco, você poderia economizar até ${amount} por mês. Compare abaixo.`,
    tableCaption: (residence: string, risk: string) =>
      `Preço mensal em ${residence} frente à oferta mais barata com risco até «${risk}»`,
    colService: "Serviço",
    colHome: (residence: string) => `Em ${residence}`,
    colBest: "Oferta mais barata aceitável",
    colMonthly: "Mensal",
    colSaving: "Economia mensal",
    noHome: "Sem preço local",
    noOffer: "Sem oferta com este risco",
    homeIsBest: "Seu país já é a opção mais barata",
    homeIsBestEvaluated: "Seu país é a opção mais barata entre as avaliadas",
    unevaluatedOffer: (country: string, price: string, diff: string | null) =>
      `Mais barata, mas sem avaliação para o seu perfil: ${country}, ${price} por mês${diff ? ` (${diff})` : ""}`,
    runnerUp: (country: string, price: string, diff: string) =>
      `Em seguida vem ${country}: ${price} por mês (${diff})`,
    runnerUpAboveRisk: (country: string, price: string, diff: string) =>
      `A mais barata de outro país é ${country}, com mais risco do que o aceito: ${price} por mês (${diff})`,
    noRunnerUp: "Não há ofertas viáveis de outros países",
    localPrice: (price: string) => `Preço local: ${price}`,
    cardViaShort: (name: string) => `com cartão ${name}`,
    cardViaBefore: "Você paga com um cartão ",
    cardViaAfter: (country: string) =>
      `, emitido em ${country}, que você abre pela internet. Sem ele não há economia.`,
    totalsTitle: "Totais",
    totalsCaption: "Totais dos serviços comparáveis",
    rowMonthly: "Mensal",
    rowYearly: "Anual",
    colPayHome: (residence: string) => `Pagando em ${residence}`,
    colOptimized: "Otimizado",
    colNetSaving: "Economia líquida",
    feeNote: (fee: string, amount: string) =>
      `O total otimizado inclui ${amount} por mês de taxa de câmbio (${fee}) por pagar em outra moeda.`,
    excludedNote: (names: string) => `Não entram nos totais (sem preço local): ${names}.`,
    noOfferNote: (names: string) => `Sem oferta com este nível de risco: ${names}.`,
    savingsByRiskTitle: "Economia conforme o risco aceito",
    resultRowsLead: (residence: string) =>
      `Para cada serviço escolhemos o país mais barato que você pode assinar com este risco; se nenhum melhora o preço, fica ${residence}.`,
    savingsByRiskHelp:
      "Com a mesma seleção, quanto do que você paga hoje você economizaria em cada nível. Toque ou arraste para mudar o nível.",
    riskSliderSaving: (pct: string) => `economia de ${pct}`,
    /** Level 0 is the official price where you live, so it reads as what you already pay. */
    riskStop: (rank: number) => (rank === 0 ? "O que você paga hoje" : `Nível ${rank}`),
    /** The same stop in the narrow slider pill. */
    riskStopShort: (rank: number) => (rank === 0 ? "Hoje" : `Nível ${rank}`),
    /** Shown under "Hoje" instead of a saving, which is always 0 there. */
    riskStopTag: "Base",
    fxNote: (date: string) => `Conversão com as taxas de câmbio de ${date}.`,
    shareNote: "A seleção fica salva no endereço da página, assim você pode compartilhá-la.",
    servicePageLink: (name: string) => `Ver todos os preços de ${name}`,
    summaryLive: (saving: string, yearly: string) =>
      `Economia líquida estimada: ${saving} por mês, ${yearly} por ano.`,
  },

  servicesIndex: {
    title: "Serviços",
    heading: "Seus serviços, sem fronteiras",
    lead: "Todos os serviços com preços coletados ou que aparecem em algum pacote.",
    caption:
      "Serviços e oferta mais barata do plano padrão conforme o risco aceito (EUR por mês, com taxa de câmbio)",
    colService: "Serviço",
    colCategory: "Categoria",
    colCountries: "Países com preço",
    colDefaultPlan: "Plano padrão",
    colHome: (residence: string) => `Em ${residence}`,
    colRisk: (rank: number) => `Risco ≤ ${rank}`,
  },

  service: {
    homepage: "Site oficial",
    defaultPlan: "Plano padrão",
    category: "Categoria",
    geoClass: "Regulamento de bloqueio geográfico",
    summaryTitle: "Mais barato por nível de risco",
    summaryCaption: (plan: string) =>
      `Oferta mais barata do plano ${plan} por nível de risco, em EUR por mês com taxa de câmbio`,
    colRisk: "Risco máximo",
    colOffer: "Oferta",
    colCost: "EUR/mês",
    colSaving: (residence: string) => `Economia frente a ${residence}`,
    offersTitle: "Preços por país",
    offersCaption: (name: string) =>
      `Todas as ofertas de ${name}, ordenadas pelo preço mensal em EUR`,
    convNote: (date: string) =>
      `«Mensal» divide o preço pelos meses do período e o converte com as taxas de câmbio de ${date}, sem taxa.`,
    colCountry: "País",
    colPlan: "Plano",
    colLocal: "Preço local",
    colMonthlyIn: (currency: string) => `Mensal (${currency})`,
    colChannel: "Canal",
    colVat: "Impostos incluídos",
    colSource: "Fonte",
    colRiskLabel: "Risco",
    colUpdated: "Última atualização",
    updatedNote: (date: string) =>
      `«Última atualização» é a data da última mudança de preço; todos foram conferidos em ${date}.`,
    noOffers: "Ainda não há uma fonte de preços para este serviço; ele aparece pelos pacotes.",
    bundlesTitle: "Pacotes que o incluem",
    noBundles: "Não há pacotes vigentes que incluam este serviço.",
    discountBundlesTitle: "Pacotes com desconto neste serviço",
    discountBundlesLead:
      "Não o incluem: dão um desconto ou meses grátis se você também o assinar, e essa economia não conta na economia do pacote.",
    feasibilityTitle: (residence: string) => `Viabilidade a partir de ${residence}`,
    feasibilityLead: (residence: string, profile?: string) =>
      `Fichas para quem mora em ${residence}${profile ? ` (perfil «${profile}»)` : ""}. As curadas à mão são sementes conservadoras, quase todas não verificadas; as do agente de pesquisa citam suas fontes e, até que uma pessoa as revise, aparecem como «Revisão pendente»; o comparador já as leva em conta.`,
    noFeasibility: (residence: string) =>
      `Não há fichas de viabilidade para este serviço: fora de ${residence} suas ofertas aparecem como «Sem avaliação».`,
    colTarget: "País de destino",
    colLabel: "Rótulo",
    colConfidence: "Confiança",
    colStatus: "Status",
    colNotes: "Notas",
    tosClause: "Cláusula dos termos",
    notesTitle: "Notas",
    backToServices: "Todos os serviços",
  },

  bundlesIndex: {
    title: "Pacotes",
    heading: "Pacotes, peça por peça",
    lead: "Pacotes promocionais que reúnem várias assinaturas por um só preço, agrupados pelo veredito para o perfil padrão e, dentro de cada grupo, da maior para a menor economia.",
    profileNote: (profile: string) =>
      `Veredito do perfil padrão: ${profile}. Ao lado, o dos demais perfis.`,
    empty: "Ainda não há pacotes vigentes registrados.",
    groupCount: (count: number) => `${count} ${count === 1 ? "pacote" : "pacotes"}`,
    caption: (label: string) => `Pacotes com o veredito «${label}» para o perfil padrão`,
    colBundle: "Pacote",
    colCountry: "País",
    colStatus: "Status",
    colMonthly: "Mensal (EUR)",
    colSaving: "Economia",
    colOthers: "Outros perfis",
    marginal: (full: string) => `custo adicional ao plano base; completo: ${full}`,
    pendingVerdict: "Com achados pendentes:",
    noOthers: "Sem outros perfis",
    cardLink: (name: string, country: string) =>
      `${name}: ficha completa na página de ${country}`,
    savingNote:
      "A economia compara o preço normal com os serviços incluídos comprados separadamente no país do pacote. Não conta a taxa de câmbio, os preços promocionais, os descontos nem os meses grátis.",
    methodologyLink: "Como os pacotes são verificados",
  },

  countriesIndex: {
    title: "Países",
    heading: "Um serviço. Preços diferentes",
    lead: "Países do catálogo com sua moeda e o número de ofertas publicadas.",
    caption: "Países, moeda e número de ofertas",
    colCountry: "País",
    colCurrency: "Moeda",
    colRegion: "Região",
    colEu: "UE/EEE",
    colOffers: "Ofertas",
  },

  country: {
    currency: "Moeda",
    region: "Região",
    eu: "UE/EEE",
    offersTitle: "Ofertas",
    offersCaption: (name: string) => `Todas as ofertas em ${name}, por serviço e plano`,
    colService: "Serviço",
    noOffers: "Ainda não há preços publicados para este país.",
    bundlesTitle: "Pacotes",
    noBundles: "Não há pacotes vigentes registrados neste país.",
    feasibilityTitle: (residence: string) => `Viabilidade a partir de ${residence}`,
    noFeasibility: "Não há fichas de viabilidade para este país.",
    backToCountries: "Todos os países",
  },

  changes: {
    title: "Mudanças de preço",
    heading: "Cada mudança, à vista",
    lead: "Mudanças detectadas nos últimos 90 dias, da mais recente para a mais antiga.",
    caption: "Mudanças de preço dos últimos 90 dias",
    colDate: "Data",
    colService: "Serviço",
    colPlan: "Plano",
    colCountry: "País",
    colChannel: "Canal",
    colKind: "Tipo",
    colOld: "Antes",
    colNew: "Depois",
    colPct: "Variação",
    empty: "Não há mudanças nos últimos 90 dias.",
  },

  methodology: {
    title: "Metodologia",
    heading: "Sem mágica. Com método",
    lead: "Que dados mostramos, de onde vêm e que riscos vale a pena entender antes de usá-los.",
    toc: "Nesta página",
    collectTitle: "Como os dados são coletados",
    collect: [
      {
        title: "Fontes oficiais primeiro",
        text: "Todos os dias um agente visita as páginas oficiais de cada serviço para cada país: a central de ajuda da Netflix, o site do Spotify, a página de suporte da Apple com os preços do iCloud+, as páginas do Apple One, Apple Music e Apple TV e o catálogo da Microsoft Store. Respeita o robots.txt, identifica-se com um User-Agent próprio, faz no máximo uma requisição a cada 3 segundos por site e nunca faz login nem resolve CAPTCHAs.",
      },
      {
        title: "App Store como alternativa",
        text: "Quando o site oficial mostra o preço conforme o IP do visitante ou só depois de entrar na conta (YouTube Premium, Google One, HBO Max, Paramount+, Crunchyroll, ChatGPT, Claude) ou o robots.txt não permite o acesso de agentes como este (Disney+), usa-se a lista pública de compras dentro do app da App Store de cada país. Essas linhas levam o canal «App Store» e a marca «pode ter acréscimo»: costumam ser mais caras que o site.",
      },
      {
        title: "Leitura determinística; o LLM só como alternativa",
        text: "Os preços são extraídos com parsers determinísticos (dados estruturados da página, seletores e o texto junto ao nome do plano). Só se o parser falhar, um modelo de linguagem lê a página salva e deve citar literalmente o texto do preço; o código confere que a citação existe na página, que a moeda é a do país e que o valor é plausível. O modelo nunca calcula nem converte valores, e essas linhas são marcadas como «LLM».",
      },
      {
        title: "Validação e valores anteriores",
        text: "São descartados valores em outra moeda ou fora de uma faixa plausível. Uma mudança de mais de 30 % fica «em revisão» até ser confirmada na execução seguinte e não é usada no comparador. Se uma página falha, mantém-se o último valor confirmado por alguns dias («valor anterior»).",
      },
      {
        title: "Câmbio e taxa",
        text: "Cada moeda é convertida pela taxa do seu banco central (via Frankfurter) quando ela é fixada: peso colombiano, argentino e chileno, libra egípcia e naira. As demais usam o Banco Central Europeu e, como alternativa, Frankfurter e ExchangeRate-API. Se todas falharem, usa-se a última tabela disponível marcada como desatualizada. As conversões são indicativas: o comparador acrescenta uma taxa de câmbio a toda oferta em uma moeda diferente da do seu país.",
      },
    ],
    feeLine: (fee: string) => `Taxa de câmbio usada no comparador: ${fee}.`,
    axesTitle: "Três eixos de risco",
    axesLead:
      "Cada ficha de viabilidade avalia três eixos separadamente. O rótulo final é o do pior eixo: um nível alto em qualquer um deles não é compensado pelos outros dois.",
    axesRules: [
      "Nas fichas curadas à mão, se algum passo exige declarar dados falsos (por exemplo, um endereço de outro país), o rótulo é no mínimo de nível 2 e o eixo de legalidade indica isso; nas fichas derivadas pelo agente de pesquisa, esse caminho é «Não viável».",
      "Os preços do seu país de residência são sempre «Oficial» (nível 0).",
      "Sem ficha, a oferta fica «Sem avaliação» e o comparador nunca a escolhe.",
      "O selo «UE» é secundário: indica que poderia existir um direito pelo Regulamento (UE) 2018/302, não que seja garantido.",
      "Um eixo «Sem dados» significa que faltam fatos com fonte: a oferta fica «Sem avaliação».",
    ],
    axisValueCaption: (axis: string) => `Valores do eixo ${axis}`,
    labelsTitle: "Rótulos",
    labelsCaption: "Rótulos de risco e seu nível",
    colLevel: "Nível",
    colLabel: "Rótulo",
    colDescription: "O que significa",
    researchTitle: "Pesquisa de viabilidade",
    researchLead:
      "Além das fichas escritas à mão, um agente pesquisa o que cada serviço exige para assinar a oferta de outro país.",
    research: [
      {
        title: "O agente só descobre",
        text: "Toda semana um agente busca com o Google Search páginas oficiais, imprensa e fóruns sobre as regras de cada serviço: país da conta, meio de pagamento, endereço, controles. Ele só propõe achados; nada do que diz conta por si só.",
      },
      {
        title: "Cada dado precisa de uma citação literal conferida",
        text: "Um achado só conta se o nosso próprio download da página citada encontra a citação literal. Esse download respeita o robots.txt e nunca tenta contornar um bloqueio, um login ou um CAPTCHA: se a página não pode ser lida, o dado não conta. Fóruns nunca contam, e a imprensa só para restrições.",
      },
      {
        title: "Fatos comuns, vereditos por perfil",
        text: "Os fatos não dependem de quem compra; o veredito sim. Um avaliador determinístico cruza os fatos com o que cada perfil pode comprovar honestamente e calcula os três eixos. Se falta um dado, o eixo fica «Sem dados» e a oferta, «Sem avaliação».",
      },
      {
        title: "O curado à mão manda",
        text: "Se existe uma ficha escrita à mão para um serviço, um país e um perfil, usa-se essa e não a do agente.",
      },
      {
        title: "Revisão humana",
        text: "Os achados novos aparecem como «Revisão pendente» até que uma pessoa os aceite. Enquanto isso, o comparador já os leva em conta e marca as ofertas que dependem deles; nas fichas eles aparecem com o seu selo e as suas citações.",
      },
      {
        title: "Nunca dados falsos",
        text: "Nas fichas derivadas pelo agente, um caminho que exigiria declarar algo falso, como um endereço ou um país que não são os seus, aparece como «Não viável». As fichas curadas à mão marcam esse passo e o eixo de legalidade diz «Exige declarar dados falsos». O site nunca sugere usar uma VPN nem dar dados falsos.",
      },
    ],
    researchRun: (date: string, run: string | null) =>
      run
        ? `Última pesquisa publicada: ${date} (execução ${run}).`
        : "Ainda não foi publicada nenhuma pesquisa: só há fichas escritas à mão.",
    profilesTitle: "Perfis",
    profilesLead:
      "Cada perfil descreve o que uma pessoa pode comprovar honestamente: residência, documentos, telefone, endereço, meios de pagamento e viagens. As fichas de serviços e países mostram o perfil padrão. O comparador começa pelo seu país, deduzido pelo fuso horário ou pelo idioma do seu navegador sem enviar nada; se você clicar em «Usar meu IP», seu navegador consulta o country.is, que recebe seu endereço IP. Depois você descreve seu perfil (país de residência entre os que têm preços, o que você tem dele e vínculos com qualquer outro país). Abaixo estão os perfis de exemplo das fichas. Também mostra as contas que você poderia abrir pela internet com essa residência e esses documentos, segundo nossos dados curados. Para um perfil próprio, os rótulos saem da aplicação das mesmas regras aos fatos pesquisados; as fichas curadas à mão só valem para o perfil para o qual foram escritas.",
    defaultProfileTag: "Padrão",
    researchNotLegal:
      "Não é assessoria jurídica: descreve o que encontramos em fontes públicas, não o que você deve fazer.",
    bundlesTitle: "Pacotes",
    bundlesLead:
      "Um pacote reúne várias assinaturas por um só preço, muitas vezes com um plano de telefonia ou uma assinatura de loja. Seus preços, serviços e condições mudam sem aviso, por isso são publicados com mais cautela que os preços diários.",
    bundles: (fee: string) => [
      {
        title: "Uma única fonte para os valores",
        text: "Os valores de cada pacote (preço, preço promocional, plano base, fidelidade, serviços incluídos, datas e requisitos) saem só de um arquivo curado à mão. Nenhum processo automático o modifica: o automático só pode trazer provas sobre esses valores ou propor mudanças, e uma pessoa as revisa.",
      },
      {
        title: "O que conta como confirmado",
        text: "Um valor só aparece como confirmado se o nosso próprio download encontrou a citação literal dele, a «âncora», na página oficial ou no PDF oficial do país do pacote há 15 dias ou menos. Se a fonte é uma tabela de preços em PDF com edições, só conta a edição vigente. Uma página bloqueada, ilegível ou de outro país não confirma nada. Se falta algo, o valor aparece como «não confirmado» e o pacote diz por quê.",
      },
      {
        title: "Verificação manual",
        text: "Uma pessoa pode conferir o pacote à mão; a data dessa conferência fica registrada. Aparece como «Verificação manual», com os dias decorridos, expira em 45 dias e nunca conta como fonte oficial.",
      },
      {
        title: "Economia sem enfeites",
        text: "A economia compara o preço normal do pacote com a soma dos serviços incluídos comprados separadamente no mesmo país, cada um pelo seu preço mais barato, então nunca é exagerada. Não conta preços promocionais, descontos nem meses grátis. Os descontos e os meses grátis aparecem à parte; um preço promocional, só quando o pacote está verificado. Quando o pacote exige um plano base, como uma linha móvel, a economia é calculada sobre o preço marginal, ou seja, o preço do pacote menos o do plano base, e a ficha indica isso.",
      },
      {
        title: "Taxa de câmbio",
        text: `Se o pacote é pago em uma moeda diferente da do seu país, a ficha mostra também o preço com a taxa de câmbio do cartão (${fee}). A porcentagem de economia não a inclui, porque compara preços do mesmo país.`,
      },
      {
        title: "Um veredito por perfil",
        text: "Cada perfil público recebe o seu veredito, com os mesmos eixos e rótulos das fichas de viabilidade. Se há uma ficha curada à mão para o pacote e o perfil, parte-se dela, e os requisitos e os serviços incluídos só podem piorá-la; aparece sempre como não verificada e com confiança baixa, porque nenhuma citação dos termos a confirma na nossa leitura das páginas. Se o pacote é do país de residência, é a oferta oficial do seu país, a menos que exija algo que o perfil não tem. Caso contrário, o veredito é composto pelos requisitos do pacote e pelo veredito, nesse país, de cada serviço incluído; se o preço dele é o de um serviço que já acompanhamos, como o Apple One, conta o veredito desse serviço. Para os demais pacotes também contam os seus próprios termos: se não encontramos neles uma regra sobre o país de residência, o eixo «Termos do serviço» fica «Sem dados», e o fato de permitirem assinar de outro país só conta com uma citação oficial. Manda o pior, e o nível do pacote nunca é menor que o dos seus serviços incluídos.",
      },
      {
        title: "Os requisitos são enunciados como fatos",
        text: "Se o pacote exige algo que o perfil não tem, como um documento de identidade, uma linha de telefone ou um cartão do país, o veredito diz isso como é e esse caminho é «Não viável». Quando o veredito é composto pelos próprios termos e requisitos do pacote, um requisito sem dados aparece como pressuposto e nunca melhora o veredito. Um requisito que os termos do pacote indicam conta assim que uma pessoa revisa esse achado, e um que facilita a assinatura só com uma citação oficial; um requisito dos dados curados nunca é relaxado. Se parte de uma ficha curada, da oferta do seu país ou do veredito de um serviço que já acompanhamos, só contam os requisitos conhecidos.",
      },
      {
        title: "Nunca dados falsos",
        text: "Nos vereditos compostos, um caminho que exigiria declarar algo que não é verdade, como uma residência ou um endereço de cobrança que não são os seus, é «Não viável». Em uma ficha curada à mão, esse passo fica marcado, o eixo de legalidade indica isso e o nível é no mínimo 2. Se a ficha curada de um serviço incluído tem um passo assim, o pacote herda esse passo, o motivo dele e, no mínimo, o nível dele. O site nunca sugere usar uma VPN, outro endereço nem dados falsos.",
      },
    ],
    bundlesNotLegal:
      "Não é assessoria jurídica: os vereditos descrevem o que encontramos em fontes públicas e o que cada perfil pode comprovar honestamente, não o que você deve fazer.",
    watchedTitle: "Pacotes monitorados",
    watchedLead:
      "Todos os pacotes publicados, inclusive os que não são monitorados: a fonte oficial, como são conferidos, o status, a última verificação automática e quantas âncoras foram verificadas. Um traço (—) indica que ainda não há nenhuma.",
    watchedCaption: "Pacotes monitorados: fonte, monitoramento, status e âncoras verificadas",
    watchedEmpty: "Não há pacotes publicados.",
    colBundle: "Pacote",
    colSource: "Fonte oficial",
    colWatch: "Monitoramento",
    colLastCheck: "Última verificação",
    colAnchors: "Âncoras verificadas",
    anchors: (verified: number, total: number) => `${verified} de ${total}`,
    anchorsNone: "Ainda sem âncoras",
    pdfTag: "PDF",
    pdfTitle: "Documento PDF oficial",
    assumptionsTitle: "Cinco pressupostos arriscados",
    assumptionsLead: "Erros frequentes ao raciocinar sobre preços de outros países:",
    assumptions: (residence: string) => [
      {
        title: "Um cartão estrangeiro não faz de você um cliente estrangeiro",
        text: "Pagar com um cartão de outro país não faz de você residente desse país. A maioria dos serviços vincula a conta ao país onde você mora ou onde se cadastrou, não ao banco que emitiu o cartão.",
      },
      {
        title: "Não se conhecerem controles não significa que seja seguro",
        text: "«Sem controle conhecido» só descreve o que foi encontrado em fontes públicas. Um serviço pode começar a verificar o país, cancelar a assinatura ou bloquear a conta a qualquer momento.",
      },
      {
        title: "O direito da UE só cobre serviços tipo nuvem e não foi testado",
        text: `O Regulamento (UE) 2018/302 exclui os serviços audiovisuais e, na prática, a música e os conteúdos protegidos. Para serviços tipo nuvem como iCloud+ ou Google One poderia se aplicar, mas o preço seria o líquido do outro país mais o IVA de ${residence}, e nenhum tribunal confirmou isso.`,
      },
      {
        title: "Os preços e as verificações de conta mudam",
        text: "Os preços sobem, os planos mudam de nome e as verificações de país ficam mais rígidas. Confira a data de cada preço e a ficha de viabilidade antes de decidir.",
      },
      {
        title: "Mudar nem sempre é reversível",
        text: "Mudar o país de uma conta Apple, Google ou Microsoft afeta toda a loja, pode fazer você perder saldo, compras ou o grupo familiar, e algumas mudanças só são permitidas uma vez por ano.",
      },
    ],
    coverageTitle: "Cobertura",
    coverageLead: (date: string) =>
      `Status de cada fonte na última execução (${date}). «Falha desde» indica desde quando uma fonte não devolve preços.`,
    coverageCaption: "Resumo de cobertura por serviço",
    coverageDetail: "Ver o detalhe por país e canal",
    coverageDetailCaption: "Cobertura por serviço, país e canal",
    colService: "Serviço",
    colTotal: "Total",
    colCountry: "País",
    colChannel: "Canal",
    colStatus: "Status",
    colAdapter: "Adaptador",
    colFailingSince: "Falha desde",
    coverageEmpty: "Não há dados de cobertura.",
    attributionsTitle: "Atribuições",
    attributionsLead: "Dados de terceiros usados com a respectiva licença:",
    license: (license: string) => `Licença: ${license}`,
    disclaimerTitle: "Não é assessoria jurídica",
    disclaimer: (profile: string) => [
      "Este site informa preços e riscos. Não é assessoria jurídica, tributária nem financeira, e não recomenda violar os termos de nenhum serviço nem declarar dados falsos.",
      `As fichas de viabilidade são sementes conservadoras, quase todas não verificadas, escritas para um perfil concreto (${profile}). Podem não se aplicar ao seu caso.`,
      "Decida por sua conta e risco e consulte sempre os termos vigentes de cada serviço.",
    ],
    axisPoints: "Nível",
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
      `residente em ${residence} com cidadania e cartão colombianos`,
  } as Record<string, (residence: string) => string>,
  unknownProfile: (id: string, residence: string) => `perfil «${id}», residente em ${residence}`,
  profileNames: {
    se_resident_co_citizen: (residence: string) =>
      `Residente em ${residence} com cidadania colombiana`,
    se_resident_eu_generic: (residence: string) =>
      `Residente em ${residence} sem vínculos com outros países`,
  } as Record<string, (residence: string) => string>,
  planNames: {
    "Móvil": "Celular",
    "Básico con anuncios": "Básico com anúncios",
    "Básico": "Básico",
    "Estándar con anuncios": "Padrão com anúncios",
    "Estándar": "Padrão",
    "Familiar": "Família",
    "Familia": "Família",
    "Estudiantes": "Estudantes",
    "Suscripción": "Assinatura",
  } as Record<string, string>,

  notFound: {
    title: "Página não encontrada",
    text: "A página que você procura não existe ou mudou de endereço.",
    back: "Voltar ao comparador",
  },

  errors: {
    missingData: (file: string) =>
      `Falta web/src/data/${file}.json. Gere os dados com "uv run haggler site-data" ou use os de exemplo com "npm run sample-data".`,
    invalidData: (problems: string[]) =>
      [
        "Os dados de web/src/data/ não cumprem o contrato de web/src/logic/types.ts (src/haggler/publish.py mudou sem atualizar types.ts e validate.ts?):",
        ...problems.map((p) => `  - ${p}`),
      ].join("\n"),
  },
} satisfies Messages;
