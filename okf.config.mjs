export const branding = {
  site: "Argos",
  bundleTitle: "Argos · Conocimiento curado",
  indexTitle: "Argos · Conocimiento",
}

export const profile = {
  id: "https://argos.local/okf/profiles/knowledge/v1",
  types: [
    "entity",
    "warning",
    "regulator",
    "source",
    "jurisdiction",
    "typology",
    "pattern",
    "guidance",
  ],
  structuralTypes: ["entity", "regulator", "source", "jurisdiction"],
  edgeLabels: [
    "Warns about",
    "Issued by",
    "Cites",
    "Operates in",
    "Same as",
    "Supersedes",
    "Part of",
    "Contains",
  ],
  inverseLabels: {
    "Part of": "Contains",
    Contains: "Part of",
    "Same as": "Same as",
  },
  knowledgeLabels: ["Warns about", "Issued by", "Cites", "Supersedes"],
  propertyGroups: [
    {
      id: "stable-identity",
      label: "Identidad estable",
      rule: "argos/knowledge-id-valid",
      appliesTo: [
        "entity",
        "warning",
        "regulator",
        "source",
        "jurisdiction",
        "typology",
        "pattern",
        "guidance",
      ],
      fields: [
        { source: "knowledge_id", graphPath: ["knowledge_id"], type: "string", required: true },
      ],
    },
    {
      id: "entity-identity",
      label: "Identidad",
      rule: "argos/entity-valid",
      appliesTo: ["entity"],
      fields: [
        { source: "entity_kind", graphPath: ["entity_kind"], type: "string", required: true,
          enum: ["domain", "phone", "email", "iban", "wallet", "handle", "company"] },
        { source: "entity_value", graphPath: ["entity_value"], type: "string", required: true },
        { source: "strength", graphPath: ["strength"], type: "string", required: true,
          enum: ["strong", "weak"] },
      ],
    },
    {
      id: "warning-state",
      label: "Estado de la advertencia",
      rule: "argos/warning-valid",
      appliesTo: ["warning"],
      fields: [
        { source: "warning_id", graphPath: ["warning_id"], type: "string", required: true },
        { source: "status", graphPath: ["status"], type: "string", required: true,
          enum: ["active", "withdrawn"] },
        { source: "captured_at", graphPath: ["captured_at"], type: "string", required: true },
      ],
    },
    {
      id: "regulator-identity",
      label: "Regulador",
      rule: "argos/regulator-valid",
      appliesTo: ["regulator"],
      fields: [
        { source: "code", graphPath: ["code"], type: "string", required: true },
      ],
    },
    {
      id: "source-provenance",
      label: "Procedencia",
      rule: "argos/source-valid",
      appliesTo: ["source"],
      fields: [
        { source: "url", graphPath: ["url"], type: "string", required: true },
        { source: "source_kind", graphPath: ["source_kind"], type: "string", required: true,
          enum: ["official-warning", "official-register", "official-guidance"] },
      ],
    },
  ],
  ruleLevels: {
    "profile/edge-label-closed": "error",
    "hygiene/unresolved-edge": "error",
    "hygiene/redundant-inverse": "error",
    "argos/knowledge-id-valid": "error",
    "argos/entity-valid": "error",
    "argos/warning-valid": "error",
    "argos/regulator-valid": "error",
    "argos/source-valid": "error",
  },
}

export const explorer = {
  injectAccess: true,
  accessTitle: "Grafo de conocimiento",
  title: "Argos · Conocimiento curado",
  knowledgeTypes: ["warning", "entity", "regulator", "source", "typology", "pattern", "guidance"],
  typeOrder: ["warning", "entity", "regulator", "source", "jurisdiction", "typology", "pattern", "guidance"],
  typeColors: {
    entity: "#3b82f6",
    warning: "#ef4444",
    regulator: "#a855f7",
    source: "#64748b",
    jurisdiction: "#0ea5e9",
    typology: "#f59e0b",
    pattern: "#d946ef",
    guidance: "#22c55e",
  },
  typeLabels: {
    entity: "Entidad",
    warning: "Advertencia",
    regulator: "Regulador",
    source: "Fuente",
    jurisdiction: "Jurisdicción",
    typology: "Tipología",
    pattern: "Patrón",
    guidance: "Guía",
  },
  edgeColors: {
    "Warns about": "#ef4444",
    "Issued by": "#a855f7",
    Cites: "#64748b",
    "Operates in": "#0ea5e9",
    "Same as": "#3b82f6",
    Supersedes: "#f59e0b",
    "Part of": "#94a3b8",
    Contains: "#94a3b8",
  },
  radius: { byType: { warning: 8, entity: 7, regulator: 6 } },
  tooltip: {
    warning: "{properties.status} · {indeg|connection|connections}",
    "*": "{indeg|incoming connection|incoming connections}",
  },
  modes: [
    {
      id: "risk",
      label: "Riesgo",
      desc: "<b>Advertencias y entidades a las que afectan.</b>",
      edges: ["Warns about", "Issued by"],
      colorBy: {
        property: "status",
        map: {
          active: { color: "#ef4444", label: "Vigente" },
          withdrawn: { color: "#64748b", label: "Retirada" },
        },
      },
    },
    {
      id: "identity",
      label: "Identidad",
      desc: "<b>Identificadores y advertencias que los conectan.</b>",
      edges: ["Warns about", "Same as"],
    },
    {
      id: "provenance",
      label: "Procedencia",
      desc: "<b>Quién emitió cada advertencia y qué fuente pública la sostiene.</b>",
      edges: ["Issued by", "Cites"],
    },
    {
      id: "history",
      label: "Historia",
      desc: "<b>Conocimiento retirado, reemplazado o sustituido.</b>",
      edges: ["Supersedes"],
    },
  ],
}

export const build = {
  content: { dir: "knowledge" },
  verify: { minNodes: 12, minEdges: 9 },
}
