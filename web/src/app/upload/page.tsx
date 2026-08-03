"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";

interface Me { orgs: { org_id: string; org_name: string }[]; }
interface Project { id: string; name: string; }

const TYPE_BY_EXT: Record<string, string> = {
  mp4: "video/mp4", mov: "video/quicktime", avi: "video/x-msvideo",
  mkv: "video/x-matroska", webm: "video/webm",
};

export default function UploadPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<"idle" | "uploading" | "starting">("idle");
  const [pct, setPct] = useState(0);
  const [error, setError] = useState("");

  useEffect(() => {
    apiGet<Me>("/auth/me").then(async (m) => {
      if (!m.orgs.length) return;
      const ps = await apiGet<Project[]>(`/orgs/${m.orgs[0].org_id}/projects`);
      setProjects(ps);
      if (ps.length) setProjectId(ps[0].id);
    }).catch((e) => setError(e.message));
  }, []);

  function contentTypeFor(f: File): string {
    if (f.type && f.type.startsWith("video/")) return f.type;
    const ext = f.name.split(".").pop()?.toLowerCase() || "";
    return TYPE_BY_EXT[ext] || "video/mp4";
  }

  async function start() {
    if (!file || !projectId) return;
    setError("");
    setPhase("uploading");
    try {
      const contentType = contentTypeFor(file);
      const { video_id, put_url } = await apiPost<{ video_id: string; put_url: string }>(
        `/projects/${projectId}/uploads`,
        { filename: file.name, content_type: contentType, byte_size: file.size },
      );

      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", put_url);
        xhr.setRequestHeader("Content-Type", contentType);
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) setPct(Math.round((e.loaded / e.total) * 100));
        };
        xhr.onload = () =>
          xhr.status < 300 ? resolve() : reject(new Error(`storage upload failed (${xhr.status})`));
        xhr.onerror = () => reject(new Error("storage upload failed — network error"));
        xhr.send(file);
      });

      setPhase("starting");
      await apiPost(`/videos/${video_id}/complete`);
      const { job_id } = await apiPost<{ job_id: string }>("/jobs", { video_id });
      router.push(`/jobs/${job_id}`);
    } catch (e: any) {
      setError(e.message);
      setPhase("idle");
    }
  }

  return (
    <div className="container" style={{ maxWidth: 560 }}>
      <h1>Upload a bug recording</h1>
      <div className="card">
        <label>Project</label>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>

        <label>Video file (mp4, mov, avi, mkv, webm — max 2 GB, 45 min)</label>
        <input type="file" accept="video/*,.mkv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />

        {file && (
          <p className="muted small">
            {file.name} — {(file.size / 1024 / 1024).toFixed(1)} MB
          </p>
        )}
        {phase === "uploading" && (
          <div className="conf-meter" style={{ margin: "12px 0" }}>
            <div style={{ width: `${pct}%`, background: "var(--accent)" }} />
          </div>
        )}
        {error && <div className="error-box">{error}</div>}
        <button onClick={start} disabled={!file || phase !== "idle"} style={{ marginTop: 12 }}>
          {phase === "idle" ? "Upload & analyze" :
           phase === "uploading" ? `Uploading… ${pct}%` : "Starting analysis…"}
        </button>
      </div>
      <p className="muted small">
        The recording is analyzed with a deterministic pipeline (FFmpeg, OpenCV, OCR).
        You review and edit every step before anything is exported.
      </p>
    </div>
  );
}
