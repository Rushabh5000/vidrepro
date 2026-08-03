// Mirrors backend/vidrepro/contracts/report.py (the canonical contract).
export interface EvidenceLink {
  frame_id: string;
  t_start_ms: number;
  t_end_ms: number;
  note?: string;
}

export interface ReproStep {
  key: string;
  index: number;
  text: string;
  action_type: string;
  grounding: "observed" | "inferred" | "assumed";
  confidence: number;
  uncertainty_note: string;
  evidence: EvidenceLink[];
  screen_state?: string;
}

export interface ReportBody {
  schema_version: "1.0";
  title: string;
  summary: string;
  bug_type: string;
  preconditions: { text: string; grounding: string; confidence: number }[];
  environment: Record<string, any>;
  steps: ReproStep[];
  observed_result: { text: string; evidence: EvidenceLink[]; confidence: number };
  expected_result: { text: string; basis: string; confidence: number } | null;
  bug_manifestation: { t_ms: number; evidence: EvidenceLink[]; confidence: number } | null;
  alternate_interpretations: string[];
  ambiguity_notes: string[];
  omitted_activity: { t_start_ms: number; t_end_ms: number; reason: string }[];
  overall_confidence: number;
  confidence_breakdown: Record<string, number>;
}

export interface JobSummary {
  job_id: string;
  video_id: string;
  filename: string;
  status: string;
  current_stage: string;
  report_id: string | null;
  created_at: string;
}

export interface Timeline {
  duration_ms: number;
  segments: { idx: number; start_ms: number; end_ms: number; transition_type: string }[];
  actions: {
    id: string; t_start_ms: number; t_end_ms: number; action_type: string;
    target_desc: string; confidence: number;
  }[];
  frames: { id: string; t_ms: number; url: string }[];
}

export const fmtTs = (ms: number) => {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
