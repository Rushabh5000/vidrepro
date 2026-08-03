"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { apiGet, sseUrl } from "@/lib/api";

const STAGES = [
  ["s01_validate", "Validate"], ["s02_normalize", "Normalize"],
  ["s03_segment", "Segment"], ["s04_frames_ocr", "Keyframes + OCR"],
  ["s05_motion", "Motion & cursor"], ["s06_events", "Event detection"],
  ["s09_bug", "Bug localization"], ["s10_synthesis", "Step synthesis"],
  ["s11_validate_score", "Validate & score"], ["s12_finalize", "Finalize"],
] as const;

interface JobState {
  status: string;
  current_stage: string;
  stages: { stage: string; status: string; ms?: number; error?: string }[];
  error: string;
  report_id: string | null;
}

export default function JobPage() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<JobState | null>(null);

  useEffect(() => {
    if (!id) return;
    const load = () => apiGet<JobState>(`/jobs/${id}`).then(setJob).catch(() => {});
    load();

    const es = new EventSource(sseUrl(`/jobs/${id}/events`));
    es.addEventListener("progress", () => load());
    es.addEventListener("snapshot", () => load());
    es.onerror = () => { /* SSE drops when job terminates server-side */ };
    const iv = setInterval(load, 8000); // fallback poll
    return () => { es.close(); clearInterval(iv); };
  }, [id]);

  if (!job) return <div className="container"><p className="muted">Loading…</p></div>;

  const done = new Set(job.stages.filter((s) => s.status === "done").map((s) => s.stage));
  const failed = new Set(job.stages.filter((s) => s.status === "failed").map((s) => s.stage));

  return (
    <div className="container" style={{ maxWidth: 640 }}>
      <h1>Processing</h1>
      <div className="card">
        <p>
          Status: <span className={`badge ${
            job.status === "failed" ? "bad" : job.status === "awaiting_review" ? "warn" : ""
          }`}>{job.status}</span>
        </p>
        <ul className="stage-list">
          {STAGES.map(([key, label]) => (
            <li key={key}>
              <span style={{ width: 20 }}>
                {failed.has(key) ? "✗" : done.has(key) ? "✓"
                  : job.current_stage === key ? "⟳" : "·"}
              </span>
              <span style={{ flex: 1 }}>{label}</span>
              <span className="muted small">
                {job.stages.find((s) => s.stage === key && s.ms !== undefined)?.ms
                  ? `${((job.stages.find((s) => s.stage === key)!.ms!) / 1000).toFixed(1)}s` : ""}
              </span>
            </li>
          ))}
        </ul>
        {job.error && <div className="error-box">{job.error}</div>}
        {job.report_id && (
          <p style={{ marginTop: 16 }}>
            <Link className="btn" href={`/reports/${job.report_id}`}>
              Open draft report in Review Studio →
            </Link>
          </p>
        )}
      </div>
    </div>
  );
}
