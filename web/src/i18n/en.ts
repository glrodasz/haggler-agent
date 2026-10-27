/**
 * Every piece of visible copy lives in one dictionary per locale; this one (English, the default
 * locale) is the type source: es.ts and pt.ts must have exactly its shape, so a missing key fails
 * the build. Pages and components never hard-code text.
 */

export const en = {
  site: {
    name: "Haggler",
    tagline: "Subscription prices by country, with their risk",
    description: (residence: string) =>
      `Compare the official prices of Netflix, Spotify, iCloud+ and more in every country, with the risk of each option if you live in ${residence}.`,
    /** Social preview card (public/og/haggler.png, drawn by scripts/brand-assets.mjs). */
    ogImageAlt: "Haggler: subscription prices by country, with their risk.",
    seo: {
      serviceTitle: (name: string) => `${name}: price in every country`,
      serviceDescription: (name: string, countries: number) =>
        `Official price of ${name} in ${countries} ${countries === 1 ? "country" : "countries"}, converted to your currency, with the risk of subscribing from outside your country of residence.`,
      countryTitle: (name: string) => `Subscription prices in ${name}`,
      countryDescription: (name: string, currency: string) =>
        `Official prices of Netflix, Spotify, iCloud+ and other subscriptions in ${name} (${currency}), local bundles and the risk of subscribing from abroad.`,
    },
    skipToContent: "Skip to content",
    navLabel: "Main",
    menuOpen: "Open menu",
    menuClose: "Close menu",
    nav: {
      home: "Compare",
      services: "Services",
      countries: "Countries",
      bundles: "Bundles",
      changes: "Changes",
      methodology: "Methodology",
    },
    sampleBanner:
      "Sample data: these prices are illustrative and do not come from a real run of the agent.",
    footer: {
      disclaimer:
        "Price and risk information, not legal advice. We never recommend giving false information.",
      dataAsOf: (date: string, run: string | null) =>
        run ? `Data as of ${date} · run ${run}` : `Data as of ${date} · no runs yet`,
      generatedAt: (when: string) => `Generated on ${when}`,
      methodologyLink: "How the data is collected",
      noAnalytics: "No analytics or cookies.",
    },
    pageEyebrow: (data: string) => `Haggler · ${data}`,
    sampleData: "Sample data",
    dataAsOfShort: (date: string) => `Data as of ${date}`,
    backToCompare: "Back to the comparison",
  },

  /** The language menu in the footer. */
  language: {
    menuLabel: "Language",
    /** aria-label of the link to this locale, read in this locale. */
    switchTo: "Switch to English",
    /** Shown next to notes that only exist in Spanish (research and curated data). */
    spanishOnly: "In Spanish",
  },

  common: {
    yes: "Yes",
    no: "No",
    unknown: "Unknown",
    officialSite: "Official site",
    source: "Source",
    open: "Open",
    sourceLinkLabel: (kind: string, what: string) => `${kind} source: ${what}`,
    externalLink: "(external link)",
    residenceTag: "Your country",
    staleFx: "Outdated exchange rate",
    staleFxNote: "* Outdated exchange rate: the latest available table was used.",
    perMonth: "/month",
    tableScrollHint: "Swipe the table sideways to see every column.",
    countryAndName: (flag: string, name: string) => `${flag} ${name}`.trim(),
  },

  periods: { month: "/month", quarter: "/quarter", year: "/year" } as Record<string, string>,
  periodNames: { month: "Monthly", quarter: "Quarterly", year: "Yearly" } as Record<string, string>,

  channels: { web: "Web", ios_iap: "App Store" } as Record<string, string>,
  channelTitles: {
    web: "Price published on the service's official website",
    ios_iap:
      "Price of the in-app purchase on the App Store; it may carry a markup over the website",
  } as Record<string, string>,

  sourceKinds: { official: "Official", secondary: "Secondary" } as Record<string, string>,

  extraction: {
    parser: "Parser",
    llm: "LLM (literal quote)",
  } as Record<string, string>,

  offerStatus: {
    accepted: "Accepted",
    quarantined: "Under review",
    rejected: "Rejected",
    carried_forward: "Previous value",
  } as Record<string, string>,
  offerStatusTitles: {
    quarantined: "Large change awaiting confirmation; not used in the comparison",
    rejected: "Discarded by validation; not used in the comparison",
    carried_forward: "The source failed today; the last confirmed value is kept",
  } as Record<string, string>,

  flags: {
    iap_maybe_marked_up: "App Store: may carry a markup",
    apple_billing: "Billed through Apple",
    secondary_disagrees: "The secondary source differs",
    iap_below_web_stale: "App Store below the website (possibly outdated)",
    iap_duplicate: "Duplicate in-app purchase",
    taxes_not_included: "Taxes not included",
    quarantine_hold: "Change under review: the previous value is shown",
  } as Record<string, string>,

  categories: {
    video: "Video",
    music: "Music",
    cloud: "Cloud storage",
    bundle: "Bundle",
    productivity: "Productivity",
    gaming: "Gaming",
    ai: "Artificial intelligence",
    education: "Education",
    other: "Other",
  } as Record<string, string>,

  regions: {
    nordics: "Nordic countries",
    eu: "European Union",
    europe_other: "Rest of Europe",
    latam: "Latin America",
    north_america: "North America",
    asia: "Asia",
    africa: "Africa",
    mena: "Middle East and North Africa",
  } as Record<string, string>,

  euGeoClass: {
    audiovisual_excluded: "Audiovisual: excluded from the Geo-blocking Regulation",
    copyright_art3_5_only: "Copyrighted works: only Articles 3 and 5 of the Regulation",
    eservice_art4_1b: "Cloud-type electronic service (Art. 4(1)(b)): EU right untested",
    software_unclear: "Software: unclear fit in the Regulation",
  } as Record<string, string>,

  /** Used when feasibility.json does not carry a label (or is older than the site). */
  riskLabels: {
    official_home: {
      name: "Official",
      description: "Price in the country where you live. No conflict with the service's terms.",
    },
    eu_right_untested: {
      name: "EU right untested",
      description:
        "Complies with the terms, but relies on a reading of Regulation (EU) 2018/302 that no court has confirmed.",
    },
    tos_breach_low_enforcement: {
      name: "Breaches ToS, no known enforcement",
      description:
        "Goes against the service's terms; no cancellations or blocks for it are known.",
    },
    tos_breach_enforced: {
      name: "Breaches ToS, actively enforced",
      description: "The service checks the country and cancels or blocks accounts that do not comply.",
    },
    not_feasible: {
      name: "Not feasible",
      description: "Not realistically obtainable with your profile.",
    },
    unknown: {
      name: "Not evaluated",
      description: "No feasibility analysis yet. Never picked as an option.",
    },
  } as Record<string, { name: string; description: string }>,
  riskLevel: (rank: number) => `Level ${rank}`,
  euBadge: "EU",
  euBadgeTitle: (residence: string) =>
    `A right under Regulation (EU) 2018/302 might exist (untested; net price + ${residence} VAT)`,

  axes: {
    legality: {
      name: "Legality",
      question: () => "Does it clash with any law?",
      values: {
        lawful: "Lawful",
        contestable_eu_right: "Contestable EU right",
        requires_misrepresentation: "Requires giving false information",
        unknown: "No data",
      },
    },
    tos: {
      name: "Terms of service",
      question: () => "Do the terms of service allow it, and is it enforced?",
      values: {
        compliant: "Complies",
        breach_unenforced: "Breaches, no known enforcement",
        breach_enforced: "Breaches, actively enforced",
        unknown: "No data",
      },
    },
    practical: {
      name: "Practicality",
      question: (residence: string) => `Can it be done from ${residence}?`,
      values: {
        remote: "Remotely",
        presence_once: "Presence in the country once",
        presence_periodic: "Regular presence",
        impossible: "Impossible",
        unknown: "No data",
      },
    },
  } as Record<
    string,
    { name: string; question: (residence: string) => string; values: Record<string, string> }
  >,

  confidence: { low: "Low", medium: "Medium", high: "High" } as Record<string, string>,
  curatedStatus: {
    verified: "Verified",
    unverified: "Unverified",
    expired: "Expired",
  } as Record<string, string>,

  /** Where a feasibility record comes from (feasibility.json records[].provenance). */
  provenance: {
    home: "Your country",
    curated: "Curated",
    composed: "From requirements",
    template: "By analogy",
    agent_reviewed: "Reviewed",
    agent_pending: "Pending review",
  } as Record<string, string>,
  provenanceTitles: {
    home: "Offer from the country where you live, assessed against the requirements it sets.",
    curated: "Hand-written record; it always prevails over automated research.",
    composed:
      "Worked out from the bundle's requirements and the verdict of each included service.",
    template:
      "Same terms and steps as the service's curated record, applied to this country without researching it specifically.",
    agent_reviewed: "Worked out from research agent findings that a person accepted.",
    agent_pending:
      "Worked out from agent findings nobody has reviewed yet. The comparison uses it, marked as pending, until a person reviews it.",
  } as Record<string, string>,
  /** Source class of a cited page (records[].evidence[].kind). */
  evidenceKinds: {
    official: "official",
    press: "press",
    forum: "forum",
    aggregator: "aggregator",
    user_report: "user report",
  } as Record<string, string>,

  /** The evidence block of a feasibility record (FeasibilityTable). */
  feasibility: {
    colSource: "Source",
    researchedOn: (date: string) => `Data from ${date}`,
    evidenceSummary: (count: number) => `Evidence (${count})`,
    noEvidence: "No citations.",
    sourceKind: (kind: string) => `${kind} source`,
    retrievedOn: (date: string) => `retrieved on ${date}`,
    verifiedQuote: "verified quote",
    unverifiedQuote: "unverified quote",
    evidenceLinkLabel: (host: string, kind: string) => `${host} (${kind} source)`,
    assumptionsTitle: "Assumptions",
    reasonsTitle: "Reasons",
  },

  coverageStatus: {
    ok: "OK",
    partial: "Partial",
    failed: "Failed",
    not_available: "Not available",
    blocked: "Blocked",
    robots_disallowed: "Disallowed by robots.txt",
    not_configured: "Not configured",
    wrong_country: "Wrong country",
  } as Record<string, string>,

  changeKinds: {
    new: "New",
    added: "New",
    changed: "Price change",
    price_change: "Price change",
    up: "Increase",
    increase: "Increase",
    down: "Decrease",
    decrease: "Decrease",
    removed: "Removed",
    dropped: "Removed",
    status: "Status change",
  } as Record<string, string>,

  bundle: {
    price: "Price",
    monthlyEur: "Monthly (EUR)",
    fullMonthlyEur: "Full (EUR)",
    marginalEur: "Extra (EUR)",
    standalone: "Separately",
    saving: "Saving",
    /** A bundle dearer than its included services bought separately (negative saving). */
    costsMore: (pct: string) => `None: it costs ${pct} more than separately`,
    /** The line under the full price of a bundle sold on top of a base plan. */
    marginalLine: (basePlan: string, amount: string) => `If you already pay for ${basePlan}: +${amount}`,
    basePlanFallback: "the base plan",
    basePlanWithPrice: (name: string, price: string) => `${name} (${price})`,
    observedOn: (service: string, date: string) => `${service} price published on ${date}`,
    observedMissing: (service: string) => `No ${service} price in the latest run.`,
    introLine: (amount: string, months: number) =>
      `Introductory price: ${amount} for ${months} ${months === 1 ? "month" : "months"}`,
    introUntil: (date: string) => `for sign-ups until ${date}`,
    firstYear: "First year (EUR)",
    firstYearValue: (amount: string) => `${amount} a month on average`,
    contract: (months: number) => `Minimum term: ${months} ${months === 1 ? "month" : "months"}`,
    withFee: (amount: string, fee: string) => `${amount} incl. ${fee} currency conversion fee`,
    partialSum: (names: string) => `partial sum: no standalone price for ${names}`,
    noStandalone: (names: string) => `No standalone price for ${names}.`,
    unverifiedSaving: "unverified",
    marginalSaving: "on the extra cost",
    savingHelp:
      "Compared with buying the included services separately, in the bundle's country. It leaves out the currency conversion fee, introductory prices, discounts and free months.",
    optionalTitle: "If you also subscribe to",
    optionalHelp: "Discounts and free months on other services; not added to the saving.",
    optionalSaving: (amount: string) => `≈ −${amount}/month`,
    optionalNoPrice: "no reference price",
    freeMonthsNote: "not spread as a monthly saving",
    components: "Includes",
    standaloneEach: (amount: string) => `${amount}/month separately`,
    confirmed: "confirmed",
    unconfirmed: "unconfirmed",
    confirmedTitle: "We found it on an official page in the bundle's country.",
    unconfirmedTitle: "We have not found it on an official page in the bundle's country yet.",
    requirements: "Requirements",
    lastVerified: (date: string) => `Last reviewed: ${date}`,
    noPrice: "Price unconfirmed",
    officialPage: "Official page",
    inclusion: {
      included: () => "Included",
      discount_pct: (v: string) => `${v} off`,
      discount_abs: (v: string) => `${v} off`,
      free_months: (v: string) => (v === "1" ? "1 month free" : `${v} months free`),
      choose_n: (n: string) => (n ? `Your choice (${n} from the group)` : "Your choice"),
    } as Record<string, (value: string) => string>,
    withAds: "with ads",
    taxIncluded: "VAT included",
    taxExcluded: "VAT not included",
    /** display_status: derived from the watcher, a manual check and the dates, never typed. */
    displayStatus: {
      verified: "Verified",
      needs_review: "Needs review",
      manual: "Checked by hand",
      unverified: "Unverified",
      expired: "Expired",
    } as Record<string, string>,
    displayStatusTitles: {
      verified:
        "Price and components found 15 days ago or less on an official page in the bundle's country.",
      needs_review:
        "The official page no longer shows a recorded value, shows a different one or published a new document: it needs reviewing.",
      manual: "A person checked it by hand 45 days ago or less. It does not count as an official source.",
      unverified: "No value is confirmed on an official page.",
      expired: "The offer ended; shown for reference only.",
    } as Record<string, string>,
    verifiedOn: (date: string) => `checked on ${date}`,
    manualAgo: (days: number) =>
      days === 0 ? "today" : `${days} ${days === 1 ? "day" : "days"} ago`,
    checkedOn: (date: string) => `last check: ${date}`,
    expiredOn: (date: string) => `ended on ${date}`,
    validFrom: (date: string) => `Available from ${date}`,
    validUntil: (date: string) => `Available until ${date}`,
    statusReasons: "Why",
    forProfile: "For your profile",
    otherProfiles: (count: number) => `Other profiles (${count})`,
    pendingVariant: "With findings pending review",
    noVerdict: "No verdict for this profile.",
    expiredNoVerdict: "Expired offer: not assessed.",
    verdictStatus: "Record status",
    verdictConfidence: "Confidence",
    perComponent: "Per included service",
    evidenceSummary: (count: number) => `Sources (${count})`,
    evidenceFacts: {
      price: "price",
      base_price: "base plan",
      intro: "introductory price",
      component: "included service",
      validity: "validity",
    } as Record<string, string>,
    evidenceVia: {
      embedded: "embedded data",
      embedded_only: "embedded data",
      text: "visible text",
      visible: "visible text",
      pdf: "PDF document",
    } as Record<string, string>,
    evidenceEdition: (edition: string) => `edition ${edition}`,
    evidenceStatus: {
      verified: "found",
      missing: "not found",
      unreadable: "unreadable page",
      embedded_only: "only in embedded data",
      outdated_document: "outdated document",
      not_checked: "not checked",
    } as Record<string, string>,
    /** pending_changes: official values that differ from the curated data, awaiting review. */
    pendingTitle: (count: number) => `Changes pending review (${count})`,
    pendingLead:
      "Values an official page in the bundle's country shows and the curated data does not yet. They change nothing until a person reviews them and edits the data.",
    pendingFields: {
      bundle_price: "Price",
      bundle_intro_price: "Introductory price",
      bundle_base_price: "Base plan price",
      bundle_intro_months: "Months at the introductory price",
      bundle_contract_months: "Minimum term",
      bundle_intro_until: "End of the introductory price",
      bundle_offer_until: "End of the offer",
      bundle_includes: "Included service",
      bundle_discount: "Discount",
      bundle_available: "Still on sale",
      bundle_price_list_url: "Price list",
    } as Record<string, string>,
    pendingSources: {
      watcher: "the watcher",
      agent: "automated research",
    } as Record<string, string>,
    pendingNone: "not recorded",
    pendingChange: (current: string, proposed: string) => `${current} → ${proposed}`,
    pendingMonths: (months: number) => `${months} ${months === 1 ? "month" : "months"}`,
    pendingDiscount: (service: string, pct: string) => `${pct} on ${service}`,
    pendingSeen: (source: string, date: string) => `according to ${source}, ${date}`,
    pendingBigChange: "large change",
    pendingBigChangeTitle: "More than 50% away from the recorded value.",
    /** watch_mode: how the bundle's official pages are checked. */
    watchModes: {
      auto: "Automatic",
      manual: "By hand",
      off: "Not watched",
    } as Record<string, string>,
    watchModeTitles: {
      auto: "Its official pages are in the weekly automatic watch; if they have not been read yet, the record says so.",
      manual: "Its pages are not read automatically: a person reviews them.",
      off: "Its values are not checked automatically.",
    } as Record<string, string>,
    expiredTitle: (count: number) => `Expired offers (${count})`,
    expiredLead: "Bundles whose offer has ended. They stay listed for a while as a reference.",
    requirementFields: {
      residency: "Residence in the country",
      billing_address: "Billing address in the country",
      signup_presence: "Presence in the country to sign up",
      local_id: "Local ID document",
      local_line: "Local line",
      local_payment: "Payment",
      credit_check: "Credit check",
      new_customers_only: "New customers only",
    } as Record<string, string>,
    requirementValues: {
      unknown: "Unknown",
      required: "Required",
      not_required: "Not required",
      checked: "Checked",
      not_checked: "Not checked",
      none: "No",
      mobile: "Mobile",
      fixed: "Landline",
      both: "Mobile and landline",
      any_card: "Any card",
      local_card: "Local card",
      local_bank: "Local bank account",
      carrier_bill: "Carrier billing",
      cedula: "Cédula (national ID)",
      personnummer: "Personnummer",
    } as Record<string, string>,
  },

  home: {
    title: "Subscription comparison",
    eyebrow: "Official prices, country by country",
    heroTitle: "Pay a fair price for your subscriptions",
    heroAccent: "a fair price",
    /** The savings tags under the hero title (islands/HeroSavings.tsx), all at level 3. */
    teasers: {
      label: (residence: string) => `Level 3 savings living in ${residence}`,
      headline: (pct: string, countries: string) => `Save ${pct} in ${countries}`,
      alone: "No ties with other countries",
      aloneTag: "no ties",
      tie: (country: string) => `With an ID, a card and regular trips to ${country}`,
      linkTitle: "See this calculation in the comparison",
    },
    lead: (residence: string) =>
      `Same service. Another country. A different price. Compare what you pay for your subscriptions in ${residence} with their official price in other countries. We tell you how much you would save, at what risk, and how much the currency conversion fee takes.`,
    ctaStart: "Work out my saving",
    ctaMethod: "How we calculate it",
    stats: {
      services: (n: number) => `${n} services`,
      countries: (n: number) => `${n} countries`,
      asOf: (date: string) => `Data as of ${date}`,
    },
    workflowTitle: "How the agent works",
    workflow: [
      { title: "Explores prices", text: "Country by country" },
      { title: "Assesses the risk", text: "No fine print" },
      { title: "Works out your saving", text: "Fees included" },
    ],
    trust: ["No sign-up", "No cookies or analytics", "We never ask for false information"],
    stepsTitle: "Three steps, one minute",
    stepsLead: "No sign-up needed. Everything is calculated in your browser.",
    steps: [
      {
        title: "Pick what you pay for",
        text: "Tick your subscriptions and the plan of each one.",
      },
      {
        title: "Tell us your situation",
        text: "Your profile, the risk you accept and the currency you think in.",
      },
      {
        title: "See your real saving",
        text: "The cheapest offer that fits you, with the currency conversion fee already in.",
      },
    ],
    compareEyebrow: "Your comparison",
    compareTitle: "Work out your saving step by step",
    compareLead: "Every change updates the result instantly.",
    howTitle: "No fine print",
    howLink: "How to read the result",
    howItems: [
      {
        title: "Official prices, no shortcuts",
        text: "Each country's price is the one the service publishes, converted to the currency you pick.",
      },
      {
        title: "You choose the risk",
        text: "An offer is only picked if its risk level is the same as or lower than the one you accept. Unevaluated offers are never picked.",
      },
      {
        title: "The saving is net",
        text: "It already takes off the currency conversion fee for paying in another currency.",
      },
      {
        title: "Your data stays with you",
        text: "Everything is calculated in your browser. Before changing anything, read the service's page and the methodology.",
      },
    ],
  },

  compare: {
    servicesLegend: "Your subscriptions",
    servicesHelp: "Tick the services you pay for and choose the plan.",
    planLabel: (service: string) => `${service} plan`,
    riskLegend: "Risk you accept",
    riskHelp:
      "Only offers with the same or a lower risk level are picked. Unevaluated offers are never picked.",
    currencyLegend: "Currency",
    currencyLabel: "Currency to show amounts in",
    currencyCommon: "Common",
    currencyAll: "All currencies",
    profileLegend: "Your profile",
    profileHelp:
      "Risk labels depend on what you can honestly prove: where you live, documents, phone, address, payment methods and travel.",
    profileLabel: "Feasibility profile",
    residenceLabel: "Which country do you live in?",
    residenceHelp: (host: string, byIp: boolean) =>
      byIp
        ? `We only list countries whose prices we track. To start from your country, your browser asks ${host} for your IP's country; we store nothing.`
        : `We only list countries whose prices we track. “Use my IP” sends your IP to ${host}, only if you press it.`,
    locateFound: {
      timezone: "We worked it out from your browser's time zone.",
      language: "We worked it out from your browser's language.",
      ip: "Based on your IP.",
    } as Record<"timezone" | "language" | "ip", string>,
    locateUncovered: (country: string) =>
      `It looks like you are in ${country}, but we do not track prices there yet. Pick the country you want to compare against.`,
    locateLoading: "Looking up your country by IP…",
    locateFailed: "We could not look up your IP. Pick your country from the list.",
    locateIp: "Use my IP",
    ownLabel: (country: string) => `What do you have from ${country}?`,
    ownDoc: "ID document or residence permit",
    tiesLabel: "Do you have ties with other countries?",
    tiesHelp:
      "Your nationality, or other countries where you have documents, a card, a phone or an address, or that you travel to. We only list countries whose prices we track.",
    tiesNone: "None.",
    tieCountry: (n: number) => `Tie country ${n}`,
    tieAdd: "Add a country…",
    countryNoMatch: "No country matches.",
    countryMatches: (n: number) =>
      n === 0 ? "No country matches." : n === 1 ? "1 country matches." : `${n} countries match.`,
    tieRemove: (country: string) => `Remove ${country}`,
    tieRemoveText: "Remove",
    tieDoc: "ID document or passport",
    tieCard: "Card issued there",
    tiePhone: "Phone number",
    tieAddress: "Real address",
    tiePresence: (country: string) => `Do you travel to ${country}?`,
    tiePresenceHelp:
      "Going regularly unlocks level 2: many offers ask you to return to the country every so often.",
    tieNoCard: (country: string) =>
      `Without a card from ${country}, almost no service there can be subscribed to.`,
    presence: { none: "I don't travel there", once: "Now and then", periodic: "Regularly" },
    adviceTitle: (country: string) => `What living in ${country} opens up`,
    adviceLead:
      "Accounts you can open online with your residence and your documents. They can give you a card from another country, which some services accept; they do not change where you live or any service's terms. The labels above already count them.",
    adviceNone: "We have no account on record that can be opened online with this profile.",
    adviceIssuer: (country: string) => `Provider from ${country}`,
    adviceCard: (country: string) => `card issued in ${country}`,
    adviceCardUnknown: "card country unconfirmed",
    adviceEea:
      "Living in the EU/EEA, your video, music and gaming subscriptions keep working when you travel temporarily to another EU country (Portability Regulation (EU) 2017/1128).",
    adviceHonest:
      "Hand-curated data, not checked in real time: check the requirements on each provider's website. Never declare a residence you do not have.",
    pendingNote: "Some offers use findings pending review: nobody has checked them yet.",
    loading: "Loading prices…",
    noscript:
      "The comparison needs JavaScript. Without it you can browse prices by service or by country.",
    noneSelected: "Pick at least one service to see the comparison.",
    resultsTitle: "Your result",
    savingYearLabel: "Net saving per year",
    savingMonthLabel: (amount: string) => `${amount} a month`,
    feeChip: (fee: string) => `${fee} currency conversion fee included`,
    totalOptimized: "Optimized monthly total",
    serviceSaving: (amount: string) => `${amount} / month`,
    shareButton: "Share my comparison",
    shareCopied: "Link copied",
    shareFailed: "Copy the page address to share it",
    detailTitle: "Breakdown by service",
    rowTotals: "Totals",
    perYear: (amount: string) => `${amount} a year`,
    savingNone: "At this risk, your country is already the cheapest option.",
    savingNoneEvaluated:
      "For your profile there are no offers from other countries with an assessed risk yet, so we add no saving.",
    savingUnevaluated: (amount: string) =>
      `Unevaluated offers from other countries would cost up to ${amount} a month less. They are not added: see them below.`,
    savingMore: (amount: string) =>
      `Accepting more risk you could save up to ${amount} a month. Compare it below.`,
    tableCaption: (residence: string, risk: string) =>
      `Monthly price in ${residence} against the cheapest offer with risk up to “${risk}”`,
    colService: "Service",
    colHome: (residence: string) => `In ${residence}`,
    colBest: "Cheapest acceptable offer",
    colMonthly: "Monthly",
    colSaving: "Monthly saving",
    noHome: "No local price",
    noOffer: "No offer at this risk",
    homeIsBest: "Your country is already the cheapest option",
    homeIsBestEvaluated: "Your country is the cheapest option among the assessed ones",
    unevaluatedOffer: (country: string, price: string, diff: string | null) =>
      `Cheaper but not evaluated for your profile: ${country}, ${price} a month${diff ? ` (${diff})` : ""}`,
    runnerUp: (country: string, price: string, diff: string) =>
      `Next is ${country}: ${price} a month (${diff})`,
    runnerUpAboveRisk: (country: string, price: string, diff: string) =>
      `The cheapest from another country is ${country}, with more risk than you accept: ${price} a month (${diff})`,
    noRunnerUp: "No viable offers from other countries",
    localPrice: (price: string) => `Local price: ${price}`,
    cardViaShort: (name: string) => `with a ${name} card`,
    cardViaBefore: "You pay with a ",
    cardViaAfter: (country: string) =>
      ` card, issued in ${country}, that you open online. Without it there is no saving.`,
    totalsTitle: "Totals",
    totalsCaption: "Totals of the comparable services",
    rowMonthly: "Monthly",
    rowYearly: "Yearly",
    colPayHome: (residence: string) => `Paying in ${residence}`,
    colOptimized: "Optimized",
    colNetSaving: "Net saving",
    feeNote: (fee: string, amount: string) =>
      `The optimized total includes ${amount} a month in currency conversion fees (${fee}) for paying in another currency.`,
    excludedNote: (names: string) => `Not added to the totals (no local price): ${names}.`,
    noOfferNote: (names: string) => `No offer at this risk level: ${names}.`,
    savingsByRiskTitle: "Saving by accepted risk",
    resultRowsLead: (residence: string) =>
      `For each service we pick the cheapest country you can subscribe from at this risk; if none beats the price, it stays in ${residence}.`,
    savingsByRiskHelp:
      "With the same selection, how much of what you pay today you would save at each level. Tap or drag to change the level.",
    riskSliderSaving: (pct: string) => `${pct} saving`,
    /** Level 0 is the official price where you live, so it reads as what you already pay. */
    riskStop: (rank: number) => (rank === 0 ? "What you pay today" : `Level ${rank}`),
    /** The same stop in the narrow slider pill. */
    riskStopShort: (rank: number) => (rank === 0 ? "Today" : `Level ${rank}`),
    /** Shown under "Today" instead of a saving, which is always 0 there. */
    riskStopTag: "Base",
    fxNote: (date: string) => `Converted with the exchange rates of ${date}.`,
    shareNote: "Your selection is kept in the page address, so you can share it.",
    servicePageLink: (name: string) => `See every ${name} price`,
    summaryLive: (saving: string, yearly: string) =>
      `Estimated net saving: ${saving} a month, ${yearly} a year.`,
  },

  visitorProfile: {
    /** Lead of the note on pages that follow the visitor's profile. */
    forYou: (residence: string, ties: string | null) =>
      `Prices and risks for your profile: you live in ${residence}${
        ties === null ? ", with no ties to other countries" : `, with ties to ${ties}`
      }.`,
    forDefault: (profile: string) => `Prices and risks for the profile “${profile}”.`,
    /** A bundle card's "For your profile" once the visitor's profile is known. */
    youLive: (residence: string, ties: string | null) =>
      `You live in ${residence}${
        ties === null ? ", with no ties to other countries" : `, with ties to ${ties}`
      }.`,
    exampleDetails: (profile: string) =>
      `The details below are for the example profile “${profile}”: reasons and evidence only exist for hand-reviewed profiles.`,
    change: "Change it in the comparison",
    feasibilityLead:
      "Labels for your profile, with the same rules as the comparison. Only the countries we have evaluated appear; records with evidence and notes only exist for hand-reviewed profiles.",
    countryFeasibilityLead:
      "Labels for your profile, with the same rules as the comparison. Only the services we have evaluated in this country appear.",
    unevaluated: (count: number) =>
      count === 1
        ? "1 more country not evaluated for your profile."
        : `${count} more countries not evaluated for your profile.`,
    unevaluatedServices: (count: number) =>
      count === 1
        ? "1 more service not evaluated for your profile."
        : `${count} more services not evaluated for your profile.`,
    noneEvaluated: "No offer from another country is evaluated for your profile yet.",
    ownCountry: "This is your country of residence: its offers are your country's official offer.",
    bundleCaption: (label: string) => `Bundles with the verdict “${label}” for your profile`,
  },

  servicesIndex: {
    title: "Services",
    heading: "Your services, without borders",
    lead: "Every service with collected prices or that appears in a bundle.",
    caption:
      "Services and the cheapest offer of the default plan by accepted risk (EUR a month, with the conversion fee)",
    colService: "Service",
    colCategory: "Category",
    colCountries: "Countries with a price",
    colDefaultPlan: "Default plan",
    colHome: (residence: string) => `In ${residence}`,
    colRisk: (rank: number) => `Level ${rank}`,
    colRiskHint: (rank: number) => `Cheapest offer at risk level ${rank} or lower`,
  },

  service: {
    homepage: "Official site",
    defaultPlan: "Default plan",
    category: "Category",
    geoClass: "Geo-blocking Regulation",
    summaryTitle: "Cheapest by risk level",
    summaryCaption: (plan: string) =>
      `Cheapest ${plan} offer by risk level, in EUR a month with the conversion fee`,
    colRisk: "Maximum risk",
    colOffer: "Offer",
    colCost: "EUR/month",
    colSaving: (residence: string) => `Saving vs ${residence}`,
    offersTitle: "Prices by country",
    offersCaption: (name: string) => `Every ${name} offer`,
    convNote: (date: string) =>
      `“Monthly” divides the price by the months in the period and converts it with the exchange rates of ${date}, with no fee.`,
    colCountry: "Country",
    colPlan: "Plan",
    colLocal: "Local price",
    colMonthlyIn: (currency: string) => `Monthly (${currency})`,
    colChannel: "Channel",
    colVat: "VAT included",
    colSource: "Source",
    colRiskLabel: "Risk",
    colUpdated: "Last updated",
    updatedNote: (date: string) =>
      `“Last updated” is the date of the last price change; all were checked on ${date}.`,
    noOffers: "There is no price source for this service yet; it appears because of bundles.",
    bundlesTitle: "Bundles that include it",
    noBundles: "No current bundles include this service.",
    discountBundlesTitle: "Bundles with a discount on this service",
    discountBundlesLead:
      "They do not include it: they give a discount or free months if you also subscribe to it, and that saving does not count towards the bundle's saving.",
    feasibilityTitle: (residence: string) => `Feasibility from ${residence}`,
    feasibilityLead: (residence: string, profile?: string) =>
      `Records for people living in ${residence}${profile ? ` (profile “${profile}”)` : ""}. The hand-curated ones are conservative seeds, almost all unverified; the research agent's ones cite their sources and, until a person reviews them, show as “Pending review”; the comparison already takes them into account.`,
    noFeasibility: (residence: string) =>
      `There are no feasibility records for this service: outside ${residence} its offers show as “Not evaluated”.`,
    colTarget: "Target country",
    colLabel: "Label",
    colConfidence: "Confidence",
    colStatus: "Status",
    colNotes: "Notes",
    tosClause: "Terms clause",
    notesTitle: "Notes",
    backToServices: "All services",
  },

  bundlesIndex: {
    title: "Bundles",
    heading: "Bundles, piece by piece",
    lead: "Promotional bundles that bring several subscriptions together for one price, grouped by their verdict for your profile and, within each group, from the largest to the smallest saving.",
    profileNote: (profile: string) =>
      `Verdict of the default profile: ${profile}. Next to it, the other profiles' verdicts.`,
    empty: "No current bundles on record yet.",
    groupCount: (count: number) => `${count} ${count === 1 ? "bundle" : "bundles"}`,
    caption: (label: string) => `Bundles with the verdict “${label}” for the default profile`,
    colBundle: "Bundle",
    colCountry: "Country",
    colStatus: "Status",
    colMonthly: "Monthly (EUR)",
    colSaving: "Saving",
    colOthers: "Other profiles",
    marginal: (full: string) => `extra cost over the base plan; full: ${full}`,
    pendingVerdict: "With pending findings:",
    noOthers: "No other profiles",
    cardLink: (name: string, country: string) => `${name}: full record on the ${country} page`,
    savingNote:
      "The saving compares the regular price with the included services bought separately in the bundle's country. It leaves out the currency conversion fee, introductory prices, discounts and free months.",
    methodologyLink: "How bundles are verified",
  },

  countriesIndex: {
    title: "Countries",
    heading: "One service. Different prices",
    lead: "Catalog countries with their currency and the number of published offers.",
    caption: "Countries, currency and number of offers",
    colCountry: "Country",
    colCurrency: "Currency",
    colRegion: "Region",
    colEu: "EU/EEA",
    colOffers: "Offers",
    comparisonTag: "In your comparison",
  },

  country: {
    currency: "Currency",
    region: "Region",
    eu: "EU/EEA",
    offersTitle: "Offers",
    offersCaption: (name: string) => `Every offer in ${name}`,
    colService: "Service",
    noOffers: "No prices published for this country yet.",
    bundlesTitle: "Bundles",
    noBundles: "No current bundles on record in this country.",
    feasibilityTitle: (residence: string) => `Feasibility from ${residence}`,
    noFeasibility: "There are no feasibility records for this country.",
    backToCountries: "All countries",
  },

  /** OfferTable: filters, sortable columns and pager (service and country pages). */
  offerTable: {
    filtersLabel: "Filter prices",
    filterAll: "All",
    clearFilters: "Clear filters",
    showing: (from: string, to: string, total: string) => `Showing ${from}–${to} of ${total} prices`,
    noMatches: "No prices match these filters.",
    pagerLabel: "Price pages",
    previous: "Previous",
    next: "Next",
    pageLabel: (n: number) => `Page ${n}`,
  },

  changes: {
    title: "Price changes",
    heading: "Every change, in plain sight",
    lead: "Changes detected in the last 90 days, newest first.",
    caption: "Price changes in the last 90 days",
    colDate: "Date",
    colService: "Service",
    colPlan: "Plan",
    colCountry: "Country",
    colChannel: "Channel",
    colKind: "Type",
    colOld: "Before",
    colNew: "After",
    colPct: "Change",
    empty: "No changes in the last 90 days.",
    filtersLabel: "Filter changes",
    filterAll: "All",
    clearFilters: "Clear filters",
    kindGroups: {
      new: "New",
      up: "Increase",
      down: "Decrease",
      removed: "Removed",
      other: "Other change",
    } as Record<string, string>,
    showing: (from: string, to: string, total: string) => `Showing ${from}–${to} of ${total} changes`,
    noMatches: "No changes match these filters.",
    pagerLabel: "Change pages",
    previous: "Previous",
    next: "Next",
    pageLabel: (n: number) => `Page ${n}`,
  },

  methodology: {
    title: "Methodology",
    heading: "No magic. Just method",
    lead: "What data we show, where it comes from and which risks are worth understanding before using it.",
    toc: "On this page",
    collectTitle: "How the data is collected",
    collect: [
      {
        title: "Official sources first",
        text: "Every day an agent visits each service's official pages for each country: Netflix's help center, Spotify's website, Apple's support page with iCloud+ prices, the Apple One, Apple Music and Apple TV pages and the Microsoft Store catalog (Microsoft 365 and Xbox Game Pass) and the Amazon Prime page of each Amazon store (Prime Video, priced as Amazon Prime, which includes it). It respects robots.txt, identifies itself with its own User-Agent, makes at most one request every 3 seconds per site and never signs in or solves CAPTCHAs.",
      },
      {
        title: "App Store as a fallback",
        text: "When the official website shows the price based on the visitor's IP or only after signing in (YouTube Premium, Google One, HBO Max, Paramount+, Crunchyroll, Duolingo, TIDAL, Deezer, Canva, Adobe Express, ChatGPT, Claude) or its robots.txt does not allow agents like this one (Disney+), the public in-app purchase list of each country's App Store is used. Those rows carry the “App Store” channel and the “may carry a markup” flag: they tend to cost more than the website.",
      },
      {
        title: "Deterministic reading; the LLM only as a fallback",
        text: "Prices are extracted with deterministic parsers (the page's structured data, selectors and the text next to the plan name). Only if the parser fails does a language model read the saved page, and it must quote the price text literally; the code checks that the quote exists on the page, that the currency is the country's and that the amount is plausible. The model never calculates or converts amounts, and those rows are marked “LLM”.",
      },
      {
        title: "Validation and previous values",
        text: "Amounts in another currency or outside a plausible range are discarded. A change of more than 30% stays “under review” until the next run confirms it and is not used in the comparison. If a page fails, the last confirmed value is kept for a few days (“previous value”).",
      },
      {
        title: "Exchange rates and fee",
        text: "Each currency is converted at its central bank's rate (via Frankfurter) when one is set: Colombian, Argentine and Chilean peso, Egyptian pound and naira. The rest use the European Central Bank and, as fallbacks, Frankfurter and ExchangeRate-API. If all fail, the latest available table is used and marked as outdated. Conversions are indicative: the comparison adds a currency conversion fee to every offer in a currency other than your country's.",
      },
    ],
    feeLine: (fee: string) => `Currency conversion fee used in the comparison: ${fee}.`,
    axesTitle: "Three risk axes",
    axesLead:
      "Each feasibility record assesses three axes separately. The final label is the worst axis: a high level on any of them is not offset by the other two.",
    axesRules: [
      "In hand-curated records, if a step requires giving false information (for example, an address in another country), the label is at least level 2 and the legality axis says so; in records derived by the research agent, that route is “Not feasible”.",
      "Prices in your country of residence are always “Official” (level 0).",
      "Without a record, the offer stays “Not evaluated” and the comparison never picks it.",
      "The “EU” badge is secondary: it means a right under Regulation (EU) 2018/302 might exist, not that it is certain.",
      "An axis with “No data” means sourced facts are missing: the offer stays “Not evaluated”.",
    ],
    axisValueCaption: (axis: string) => `Values of the ${axis} axis`,
    labelsTitle: "Labels",
    labelsCaption: "Risk labels and their level",
    colLevel: "Level",
    colLabel: "Label",
    colDescription: "What it means",
    researchTitle: "Feasibility research",
    researchLead:
      "Besides the hand-written records, an agent researches what each service requires to subscribe to another country's offer.",
    research: [
      {
        title: "The agent only discovers",
        text: "Every week an agent uses Google Search to find official pages, press and forums about each service's rules: account country, payment method, address, checks. It only proposes findings; nothing it says counts on its own.",
      },
      {
        title: "Every fact needs a checked literal quote",
        text: "A finding only counts if our own download of the cited page finds the literal quote. That download respects robots.txt and never tries to get around a block, a sign-in or a CAPTCHA: if the page cannot be read, the fact does not count. Forums never count, and the press only counts for restrictions.",
      },
      {
        title: "Shared facts, per-profile verdicts",
        text: "Facts do not depend on who is buying; the verdict does. A deterministic evaluator crosses the facts with what each profile can honestly prove and works out the three axes. If a fact is missing, the axis stays “No data” and the offer “Not evaluated”.",
      },
      {
        title: "Hand-curated wins",
        text: "If there is a hand-written record for a service, a country and a profile, that one is used instead of the agent's.",
      },
      {
        title: "Human review",
        text: "New findings show as “Pending review” until a person accepts them. Meanwhile, the comparison already takes them into account and marks the offers that depend on them; on the records they appear with their badge and their quotes.",
      },
      {
        title: "Never false information",
        text: "In records derived by the agent, a route that would require declaring something false, such as an address or a country that is not yours, shows as “Not feasible”. Hand-curated records mark that step and their legality axis says “Requires giving false information”. The site never suggests using a VPN or giving false information.",
      },
    ],
    researchRun: (date: string, run: string | null) =>
      run
        ? `Latest published research: ${date} (run ${run}).`
        : "No research has been published yet: there are only hand-written records.",
    profilesTitle: "Profiles",
    profilesLead: (byIp: boolean) =>
      `Each profile describes what a person can honestly prove: residence, documents, phone, address, payment methods and travel. Every page (comparison, services, countries, bundles) shows prices and risks for your profile. On arrival we start from your country, with no ties: ${
        byIp
          ? "your browser asks country.is for your IP address's country (country.is receives the IP; we store nothing); if it doesn't answer, we work it out from your browser's time zone or language"
          : "we work it out from your browser's time zone or language without sending anything; if you press “Use my IP”, your browser asks country.is, which receives your IP address"
      }. Then you describe your profile in the comparison (country of residence among those with prices, what you have from it, and ties with any other country); we keep it only in your browser (local storage, never sent and not a cookie) so the other pages can use it. Without JavaScript, pages show the sample profile below. The comparison also shows the accounts you could open online with that residence and those documents, according to our curated data. For your own profile, the labels come from applying the same rules to the researched facts; hand-curated records only apply to the profile they were written for.`,
    defaultProfileTag: "Default",
    researchNotLegal:
      "Not legal advice: it describes what we found in public sources, not what you should do.",
    bundlesTitle: "Bundles",
    bundlesLead:
      "A bundle brings several subscriptions together for one price, often with a phone plan or a store subscription. Their prices, services and conditions change without notice, so they are published more cautiously than daily prices.",
    bundles: (fee: string) => [
      {
        title: "One single source for values",
        text: "Each bundle's values (price, introductory price, base plan, minimum term, included services, dates and requirements) come only from a hand-curated file. No automated process changes it: automation can only bring evidence about those values or propose changes, and a person reviews them.",
      },
      {
        title: "What counts as confirmed",
        text: "A value only shows as confirmed if our own download found its literal quote, the “anchor”, on the official page or official PDF in the bundle's country 15 days ago or less. If the source is a PDF price list with editions, only the current edition counts. A blocked, unreadable or other-country page confirms nothing. If something is missing, the value shows as “unconfirmed” and the bundle says why.",
      },
      {
        title: "Checked by hand",
        text: "A person can check the bundle by hand; the date of that check is recorded. It shows as “Checked by hand”, with the days elapsed, expires after 45 days and never counts as an official source.",
      },
      {
        title: "Saving without embellishment",
        text: "The saving compares the bundle's regular price with the sum of the included services bought separately in the same country, each at its cheapest price, so it is never exaggerated. It leaves out introductory prices, discounts and free months. Discounts and free months are shown separately; an introductory price, only when the bundle is verified. When the bundle requires a base plan, such as a mobile line, the saving is calculated on the marginal price, that is, the bundle's price minus the base plan's, and the record says so.",
      },
      {
        title: "Currency conversion fee",
        text: `If the bundle is paid in a currency other than your country's, the record also shows the price with the card's currency conversion fee (${fee}). The saving percentage leaves it out, because it compares prices within the same country.`,
      },
      {
        title: "One verdict per profile",
        text: "Each public profile gets its verdict, with the same axes and labels as the feasibility records. If there is a hand-curated record for the bundle and the profile, it is the starting point, and the requirements and included services can only make it worse; it is always shown as unverified and with low confidence, because no quote from the terms confirms it in our reading of the pages. If the bundle is from the country of residence, it is your country's official offer, unless it requires something the profile does not have. Otherwise, the verdict is composed from the bundle's requirements and the verdict, in that country, of each included service; if its price is that of a service we already track, such as Apple One, that service's verdict counts. For the other bundles their own terms count too: if we do not find a rule about the country of residence in them, the “Terms of service” axis stays “No data”, and their allowing a subscription from another country only counts with an official quote. The worst wins, and the bundle's level is never lower than that of its included services.",
      },
      {
        title: "Requirements are stated as facts",
        text: "If the bundle requires something the profile does not have, such as an ID document, a phone line or a card from the country, the verdict says so plainly and that route is “Not feasible”. When the verdict is composed from the bundle's own terms and requirements, a requirement with no data shows as an assumption and never improves the verdict. A requirement the bundle's terms state counts as soon as a person reviews that finding, and one that makes subscribing easier only with an official quote; a requirement from the curated data is never relaxed. If it starts from a curated record, from your country's offer or from the verdict of a service we already track, only the known requirements count.",
      },
      {
        title: "Never false information",
        text: "In composed verdicts, a route that would require declaring something untrue, such as a residence or a billing address that is not yours, is “Not feasible”. In a hand-curated record, that step is marked, the legality axis says so and the level is at least 2. If an included service's curated record has such a step, the bundle inherits that step, its reason and, at least, its level. The site never suggests using a VPN, another address or false information.",
      },
    ],
    bundlesNotLegal:
      "Not legal advice: the verdicts describe what we found in public sources and what each profile can honestly prove, not what you should do.",
    watchedTitle: "Watched bundles",
    watchedLead:
      "Every published bundle, including the ones that are not watched: its official source, how it is checked, its status, its last automatic check and how many of its anchors have been verified. A dash (—) means there is none yet.",
    watchedCaption: "Watched bundles: source, watch, status and verified anchors",
    watchedEmpty: "No published bundles.",
    colBundle: "Bundle",
    colSource: "Official source",
    colWatch: "Watch",
    colLastCheck: "Last check",
    colAnchors: "Verified anchors",
    anchors: (verified: number, total: number) => `${verified} of ${total}`,
    anchorsNone: "No anchors yet",
    pdfTag: "PDF",
    pdfTitle: "Official PDF document",
    assumptionsTitle: "Five risky assumptions",
    assumptionsLead: "Common mistakes when reasoning about other countries' prices:",
    assumptions: (residence: string) => [
      {
        title: "A foreign card does not make you a foreign customer",
        text: "Paying with a card from another country does not make you a resident of that country. Most services tie the account to the country where you live or where you signed up, not to the bank that issued the card.",
      },
      {
        title: "No known checks does not mean it is safe",
        text: "“No known enforcement” only describes what we found in public sources. A service can start checking the country, cancel the subscription or block the account at any time.",
      },
      {
        title: "The EU right only covers cloud-type services and is untested",
        text: `Regulation (EU) 2018/302 excludes audiovisual services and, in practice, music and copyrighted content. For cloud-type services such as iCloud+ or Google One it might apply, but the price would be the other country's net price plus ${residence} VAT, and no court has confirmed it.`,
      },
      {
        title: "Prices and account checks change",
        text: "Prices go up, plans get renamed and country checks get stricter. Check the date of each price and the feasibility record before deciding.",
      },
      {
        title: "Switching is not always reversible",
        text: "Changing the country of an Apple, Google or Microsoft account affects the whole store, can make you lose balance, purchases or your family group, and some changes are only allowed once a year.",
      },
    ],
    coverageTitle: "Coverage",
    coverageLead: (date: string) =>
      `Status of each source in the latest run (${date}). “Failing since” shows since when a source has not returned prices.`,
    coverageCaption: "Coverage summary by service",
    coverageDetail: "See the detail by country and channel",
    coverageDetailCaption: "Coverage by service, country and channel",
    colService: "Service",
    colTotal: "Total",
    colCountry: "Country",
    colChannel: "Channel",
    colStatus: "Status",
    colAdapter: "Adapter",
    colFailingSince: "Failing since",
    coverageEmpty: "No coverage data.",
    attributionsTitle: "Attributions",
    attributionsLead: "Third-party data used under its license:",
    license: (license: string) => `License: ${license}`,
    disclaimerTitle: "Not legal advice",
    disclaimer: (profile: string) => [
      "This site reports prices and risks. It is not legal, tax or financial advice, and it does not recommend breaching any service's terms or giving false information.",
      `The feasibility records are conservative seeds, almost all unverified, written for a specific profile (${profile}). They may not apply to your case.`,
      "Decide at your own risk and always check each service's current terms.",
    ],
    axisPoints: "Level",
    axisValue: "Value",
    tompecFallback: {
      name: "Netflix prices dataset, Thomas Pecserke (tompec/netflix-prices)",
      url: "https://github.com/tompec/netflix-prices",
      license: "CC BY 4.0",
    },
  },

  /** meta.profile -> who the feasibility records were written for (residence from meta). */
  profiles: {
    se_resident_co_citizen: (residence: string) =>
      `resident in ${residence} with Colombian citizenship and card`,
  } as Record<string, (residence: string) => string>,
  unknownProfile: (id: string, residence: string) => `profile “${id}”, resident in ${residence}`,
  /**
   * Public profile names (feasibility.json profiles[].name_es is Spanish); a profile missing here
   * keeps its name from the data.
   */
  profileNames: {
    se_resident_co_citizen: (residence: string) =>
      `Resident in ${residence} with Colombian citizenship`,
    se_resident_eu_generic: (residence: string) =>
      `Resident in ${residence} with no ties to other countries`,
  } as Record<string, (residence: string) => string>,
  /**
   * Plan names by their Spanish catalog label (services.json plans[].label_es); a label missing
   * here (sizes, brand names) is shown as is.
   */
  planNames: {
    "Móvil": "Mobile",
    "Básico con anuncios": "Basic with ads",
    "Básico": "Basic",
    "Estándar con anuncios": "Standard with ads",
    "Estándar": "Standard",
    "Familiar": "Family",
    "Familia": "Family",
    "Estudiantes": "Student",
    "Suscripción": "Subscription",
  } as Record<string, string>,

  notFound: {
    title: "Page not found",
    text: "The page you are looking for does not exist or has moved.",
    back: "Back to the comparison",
  },

  errors: {
    missingData: (file: string) =>
      `web/src/data/${file}.json is missing. Generate the data with "uv run haggler site-data" or use the sample with "npm run sample-data".`,
    invalidData: (problems: string[]) =>
      [
        "The data in web/src/data/ does not match the contract in web/src/logic/types.ts (did src/haggler/publish.py change without updating types.ts and validate.ts?):",
        ...problems.map((p) => `  - ${p}`),
      ].join("\n"),
  },
};

export type Messages = typeof en;
