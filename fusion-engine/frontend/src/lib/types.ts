export type Region = { id: string; name: string; lat: number; lon: number; radius_nm: number; user?: boolean };

export type Event = {
  id: string; lat: number; lon: number; root_label: string; place: string; country?: string;
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

export type Firms = { lat: number; lon: number; novelty?: number; ts: string; frp?: number; satellite?: string; daynight?: string };
export type Sar = { lat: number; lon: number; length_m?: number; contrast?: number; ts: string };
export type Tail = { coords: [number, number][]; military?: boolean; callsign?: string; r?: string; hex?: string; t?: string };

export type GraphNode = { id: string; kind: string; label: string; lat?: number; lon?: number; military?: boolean; mentions?: number; severity?: number; x?: number; y?: number; vx?: number; vy?: number; fx?: number | null; fy?: number | null };
export type GraphLink = { source: string; target: string; kind?: string; score?: number };
export type Graph = { nodes: GraphNode[]; links: GraphLink[] };

export type Status = {
  counts: { events: number; conflict_events: number; tracks: number; military_tracks: number; alerts: number; social?: number; firms?: number; firms_novel?: number };
  updated?: string; gdelt_window?: string; store?: string;
};

export type ReplayScenario = { id: string; title: string; notes?: string; center: [number, number]; zoom: number; day: string; bbox: [number, number, number, number]; sources?: [string, string][] };
export type ReplayConfig = { scenario: ReplayScenario; t_min: number; t_max: number; n_events: number; n_aircraft: number; n_military: number; n_firms?: number; n_sar?: number; adsb_available?: boolean };
export type ReplaySnapshot = { events: Event[]; tracks: Track[]; alerts: Alert[]; graph: Graph; tails?: Tail[]; firms?: Firms[]; sar?: Sar[]; sar_scene?: { n: number; label: string; ts: string }; counts: Status["counts"]; t_iso: string };
export type Entity = { neighbors?: Array<{ kind: string; label: string }> };

export type LivePicture = { status: Status; events: Event[]; tracks: Track[]; alerts: Alert[]; firms: Firms[] };
