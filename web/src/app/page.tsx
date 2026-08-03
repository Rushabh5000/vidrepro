"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { JobSummary } from "@/lib/types";

interface Me {
  orgs: { org_id: string; org_name: string; role: string }[];
}
interface Project { id: string; name: string; }

const STATUS_BADGE: Record<string, string> = {
  awaiting_review: "warn", completed: "ok", failed: "bad", running: "", queued: "",
};

export default function Dashboard() {
  const [orgId, setOrgId] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    apiGet<Me>("/auth/me").then((m) => {
      if (m.orgs.length) setOrgId(m.orgs[0].org_id);
    }).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!orgId) return;
    apiGet<Project[]>(`/orgs/${orgId}/projects`).then((ps) => {
      setProjects(ps);
      if (ps.length) setProjectId(ps[0].id);
    }).catch((e) => setError(e.message));
  }, [orgId]);

  useEffect(() => {
    if (!projectId) return;
    const load = () =>
      apiGet<JobSummary[]>(`/jobs?project_id=${projectId}`).then(setJobs).catch(() => {});
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
  }, [projectId]);

  return (
    <div className="container">
      <h1>Dashboard</h1>
      {error && <div className="error-box">{error}</div>}
      <div className="row" style={{ marginBottom: 16 }}>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} style={{ maxWidth: 240 }}>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <Link href="/upload" className="btn">+ Upload recording</Link>
      </div>

      <div className="card">
        <h2>Processing jobs</h2>
        {jobs.length === 0 && <p className="muted">No jobs yet — upload a bug recording to start.</p>}
        {jobs.length > 0 && (
          <table>
            <thead>
              <tr><th>Recording</th><th>Status</th><th>Stage</th><th>Created</th><th></th></tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.job_id}>
                  <td>{j.filename || j.video_id.slice(0, 8)}</td>
                  <td><span className={`badge ${STATUS_BADGE[j.status] ?? ""}`}>{j.status}</span></td>
                  <td className="muted small">{j.current_stage}</td>
                  <td className="muted small">{new Date(j.created_at).toLocaleString()}</td>
                  <td>
                    {j.report_id
                      ? <Link href={`/reports/${j.report_id}`}>Open report</Link>
                      : <Link href={`/jobs/${j.job_id}`}>View progress</Link>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
