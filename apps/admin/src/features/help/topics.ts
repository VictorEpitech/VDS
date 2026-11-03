export const HELP_TOPICS = ["screens", "content", "playlists", "layouts", "schedule", "override", "power", "agents", "publication", "inheritance"] as const;
export type HelpTopic = (typeof HELP_TOPICS)[number];

export const GLOSSARY_KEYS = ["override", "group", "inheritance", "schedule", "layout", "scheduled_off", "power_driver", "site_agent", "draft", "in_review"] as const;
export type GlossaryKey = (typeof GLOSSARY_KEYS)[number];
