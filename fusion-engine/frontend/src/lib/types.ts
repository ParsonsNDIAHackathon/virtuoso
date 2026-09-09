export type Region = { id: string; name: string; lat: number; lon: number; radius_nm: number; user?: boolean };

export type Event = {
  id: string; lat: number; lon: number; root_label: string; place: string; country?: string;
  ts?: string;
  is_conflict?: boolean; source_domain?: string; url?: string; goldstein?: number; tone?: number;
  num_mentions?: number; actor1?: string; actor2?: string; persons?: string[]; orgs?: string[]; themes?: string[];
};

export type Track = {
  id: string; lat: number; lon: number; military?: boolean; callsign?: string; registration?: string;
  hex?: string; ac_type?: string; alt_ft?: number; gs_kt?: number; track_deg?: number; squawk?: string;
  source?: string; rssi?: number; messages?: number; ts?: string;
};

export type Alert = {
  aircraft_id: string; event_id: string; aircraft_label: string; event_label: string; score: number;
  distance_km: number; dt_min?: number; reason?: string; lat: number; lon: number;
};

export type Firms = { id: string; lat: number; lon: number; novelty?: number; ts: string; frp?: number; satellite?: string; daynight?: string };
export type Sar = { lat: number; lon: number; length_m?: number; contrast?: number; ts: string };
export type Tail = { coords: [number, number][]; military?: boolean; callsign?: string; r?: string; hex?: string; t?: string };

export type GraphNode = { id: string; kind: string; label: string; lat?: number; lon?: number; military?: boolean; mentions?: number; severity?: number; x?: number; y?: number; vx?: number; vy?: number; fx?: number | null; fy?: number | null };
export type GraphLink = { source: string; target: string; kind?: string; score?: number };
export type Graph = { nodes: GraphNode[]; links: GraphLink[] };

export type Status = {
  counts: { events: number; conflict_events: number; tracks: number; military_tracks: number; alerts: number; social?: number; firms?: number; firms_novel?: number; candidates?: number; assessments?: number; supported?: number; plausible?: number; clusters?: number };
  updated?: string; gdelt_window?: string; store?: string;
  sources?: Record<string, { state: "starting" | "ready" | "partial" | "error"; label?: string; updated?: string; count?: number; detail?: string }>;
};

export type ReplayScenario = { id: string; title: string; notes?: string; center: [number, number]; zoom: number; day: string; days?: string[]; bbox: [number, number, number, number]; sources?: [string, string][] };
export type ReplayConfig = { scenario: ReplayScenario; t_min: number; t_max: number; days?: string[]; layers_loaded?: Record<string, string[]>; n_events: number; n_aircraft: number; n_military: number; n_firms?: number; n_sar?: number; adsb_available?: boolean; sar_scenes?: string[]; sar_summary?: Array<{ ts: string; n: number; core: number; scene: string }> };

// Curated (manual) evidence: analyst-reviewed records served by /api/replay/{id}/evidence. Claims are
// claims, not observations: no coordinates, no scores, timing kept at the record's own precision.
export type CuratedVessel = { id: string; name: string; imo: string; vessel_type: string; flag: string; mmsi_candidate?: string | null; callsign_candidate?: string | null };
export type CuratedSource = { id: string; publisher: string; url: string; source_type: string; publication_date?: string | null; publication_time_utc?: string | null; as_of_date?: string | null; retrieved_date?: string | null };
export type CuratedClaim = { id: string; vessel_id: string; source_ids: string[]; event_date?: string | null; event_time_utc?: string | null; time_precision: "date_only" | "ambiguous_overnight" | "minute_as_reported"; location_text?: string | null; coordinates: null | [number, number]; claim: string; evidence_class: string; attacker?: string | null };
export type CuratedLead = { url: string; platform: string; publisher: string; publication_time_utc?: string | null; original_language: string; summary_en: string; summary_kind?: string; status?: string };
export type Evidence = { kind: string; prepared_date?: string; retrieved_date?: string; vessels: CuratedVessel[]; sources: CuratedSource[]; claims: CuratedClaim[]; leads: CuratedLead[]; excluded: Array<{ reason?: string }>; notes: string[]; window?: { t_min: number; t_max: number; label?: string } };
export type RecordRef = { kind: "gdelt" | "telegram" | "adsb" | "firms" | string; id: string };
export type FusionCandidate = { id: string; left_id: string; left_kind: string; right_id: string; right_kind: string; distance_km: number; dt_min: number; candidate_score: number; reasons: string[]; entity_overlap?: string[] };
export type ArticleMatch = { status: "SAME_ARTICLE" | "NOT_ESTABLISHED" | "NOT_APPLICABLE"; basis: "normalized_url" | "redirect_url" | null; confidence: number | null; shared_url: string | null; independent_corroboration: boolean | null };
export type SourceDocument = { record_id: string; url: string; resolved_url: string; title: string | null; available: boolean; characters: number; truncated: boolean; retrieved_at: string | null; limitation: string | null };
export type Assessment = { id: string; candidate_id: string; left_id: string; left_kind: string; right_id: string; right_kind: string; verdict: "SUPPORTED" | "PLAUSIBLE" | "INSUFFICIENT_EVIDENCE" | "CONTRADICTED"; relation: string; evidence_strength: number; supporting_facts: string[]; strongest_limitation: string; rationale: string; resolved_entities?: Array<{ record_id: string; name: string; canonical_name: string; entity_type: string; confidence: number }>; model: string; prompt_version: string; created_at: string; cached: boolean; needs_review: boolean; distance_km: number; dt_min: number; incident_relationship?: "SAME_INCIDENT" | "RELATED_INCIDENTS" | "UNRELATED" | "UNCERTAIN" | "NOT_APPLICABLE"; article_match?: ArticleMatch; has_article_match?: boolean; source_documents?: SourceDocument[]; source_groups?: Record<string, string> };
export type FusionCluster = { id: string; record_ids: string[]; assessment_ids: string[]; modalities: string[]; score: number; needs_review: boolean; brief?: string | null; caveats: string[] };
export type AIStatus = { provider: "openai"; model: string; configured: boolean; prompt_version: string };
export type ReplaySnapshot = { t: number; events: Event[]; tracks: Track[]; alerts: Alert[]; graph: Graph; tails?: Tail[]; firms?: Firms[]; sar?: Sar[]; sar_scene?: { n: number; label: string; ts: string }; sar_core?: Sar[]; sar_core_scene?: { n: number; label: string; ts: string }; candidates?: FusionCandidate[]; assessments?: Assessment[]; clusters?: FusionCluster[]; counts: Status["counts"]; t_iso: string };
export type EntityNode = { id: string; kind: "event" | "actor" | "location" | "source" | "aircraft" | string; label: string; military?: boolean };
export type Entity = { node: EntityNode; neighbors: EntityNode[]; links: Array<{ source: string; target: string; kind?: string }> };
export type LinkPreview = { url: string; host: string; title?: string | null; description?: string | null; image?: string | null; site_name?: string | null; published?: string | null; embeddable?: boolean; error?: string | null; status?: number };

export type LivePicture = { status: Status; events: Event[]; tracks: Track[]; alerts: Alert[]; firms: Firms[] };

export type SocialPlatform = { id: string; label: string; targets: string[]; count: number; status?: string };
export type SocialPlatforms = { platforms: SocialPlatform[]; total: number };

export type SourcePreview = {
  url: string; final_url?: string; site?: string; title?: string | null; description?: string | null;
  image?: string | null; text?: string; fetched_at?: string; error?: string | null;
};

export type Viewport = { west: number; south: number; east: number; north: number; zoom: number };
// Live bins: flows (events, social, alerts, firms_new) are sums per 15-min bin; tracks/military are
// levels averaged from this server's own fuse history and null where nothing was recorded yet.
export type TimelineBin = { t: number; events: number; conflict: number; social: number; tracks: number | null; military: number | null; alerts?: number; firms_new: number; backfilled?: boolean };
export type Timeline = {
  bins: TimelineBin[]; step_min: number; hours?: number; t_min?: number; sar_scenes?: Array<{ t: number; ts: string; n: number }>;
  backfill?: { status: string; hours: number; windows: number }; since?: number | null;
};
