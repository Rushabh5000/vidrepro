"use client";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiPost, apiPut } from "@/lib/api";
import { ReportBody, ReproStep, Timeline, fmtTs } from "@/lib/types";

interface ReportResponse {
  report_id: string;
  job_id: string;
  video_id: string | null;
  status: string;
  revision_id: string | null;
  body: ReportBody | null;
}

const ACTION_ICON: Record<string, string> = {
  click: "🖱", tap: "👆", type: "⌨", scroll: "↕", navigate: "🧭",
  dialog_open: "🗔", form_submit: "📨", swipe: "👉",
};

function confColor(c: number) {
  return c >= 0.8 ? "var(--ok)" : c >= 0.6 ? "var(--warn)" : "var(--bad)";
}

export default function ReviewStudio() {
  const { id } = useParams<{ id: string }>();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [body, setBody] = useState<ReportBody | null>(null);
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [videoUrl, setVideoUrl] = useState("");
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [playhead, setPlayhead] = useState(0);
  const [exportOut, setExportOut] = useState("");

  const load = useCallback(async () => {
    const r = await apiGet<ReportResponse>(`/reports/${id}`);
    setReport(r);
    setBody(r.body);
    setDirty(false);
    if (r.job_id) apiGet<Timeline>(`/jobs/${r.job_id}/timeline`).then(setTimeline).catch(() => {});
    if (r.video_id) {
      apiGet<{ url: string }>(`/videos/${r.video_id}/media?kind=proxy`)
        .then((m) => setVideoUrl(m.url)).catch(() => {});
    }
  }, [id]);

  useEffect(() => { load().catch((e) => setError(e.message)); }, [load]);

  const seek = (ms: number) => {
    if (videoRef.current) videoRef.current.currentTime = ms / 1000;
  };

  const patchStep = (key: string, patch: Partial<ReproStep>) => {
    if (!body) return;
    setBody({
      ...body,
      steps: body.steps.map((s) => (s.key === key ? { ...s, ...patch } : s)),
    });
    setDirty(true);
  };

  const deleteStep = (key: string) => {
    if (!body) return;
    setBody({
      ...body,
      steps: body.steps.filter((s) => s.key !== key).map((s, i) => ({ ...s, index: i + 1 })),
    });
    setDirty(true);
  };

  const mergeWithNext = (key: string) => {
    if (!body) return;
    const i = body.steps.findIndex((s) => s.key === key);
    if (i < 0 || i + 1 >= body.steps.length) return;
    const a = body.steps[i], b = body.steps[i + 1];
    const merged: ReproStep = {
      ...a,
      text: `${a.text.replace(/\.$/, "")}, then ${b.text.charAt(0).toLowerCase()}${b.text.slice(1)}`,
      evidence: [...a.evidence, ...b.evidence],
      confidence: Math.min(a.confidence, b.confidence),
    };
    const steps = [...body.steps.slice(0, i), merged, ...body.steps.slice(i + 2)]
      .map((s, n) => ({ ...s, index: n + 1 }));
    setBody({ ...body, steps });
    setDirty(true);
  };

  async function save() {
    if (!body || !report?.revision_id) return;
    setError(""); setMsg("");
    try {
      await apiPut(`/reports/${id}`, {
        body, change_note: "edited in review studio", parent_revision_id: report.revision_id,
      });
      setMsg("Saved as new revision.");
      await load();
    } catch (e: any) { setError(e.message); }
  }

  async function approve() {
    try { await apiPost(`/reports/${id}/approve`); setMsg("Report approved."); await load(); }
    catch (e: any) { setError(e.message); }
  }

  async function doExport(format: string) {
    setError(""); setExportOut(""); setMsg("");
    try {
      const res = await apiPost<any>(`/reports/${id}/exports`, { format });
      if (format === "ado") {
        setExportOut(`Azure DevOps Bug #${res.work_item_id} created`);
        setMsg(res.url);
      } else if (res.content) {
        setExportOut(res.content);
        if (res.download_url) setMsg(res.download_url);
      } else if (res.download_url) {
        window.open(res.download_url, "_blank");
      } else if (res.issue) {
        setExportOut(JSON.stringify(res.issue, null, 2));
      }
    } catch (e: any) { setError(e.message); }
  }

  if (!body) {
    return (
      <div className="container">
        {error ? <div className="error-box">{error}</div> : <p className="muted">Loading…</p>}
      </div>
    );
  }

  const duration = timeline?.duration_ms || 1;
  const frameById = new Map((timeline?.frames ?? []).map((f) => [f.id, f]));

  return (
    <div className="container">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1 style={{ marginBottom: 8 }}>{body.title}</h1>
        <span className="badge" style={{ borderColor: confColor(body.overall_confidence) }}>
          overall confidence {body.overall_confidence.toFixed(2)}
        </span>
      </div>
      <p className="muted">{body.summary} · status: {report?.status}</p>
      {error && <div className="error-box">{error}</div>}
      {msg && <div className="card small">{msg.startsWith("http")
        ? <a href={msg} target="_blank" rel="noreferrer">{msg}</a> : msg}</div>}

      <div className="grid2">
        {/* ── video + timeline pane ── */}
        <div>
          <div className="card">
            {videoUrl
              ? <video ref={videoRef} src={videoUrl} controls style={{ width: "100%", borderRadius: 8 }}
                       onTimeUpdate={(e) => setPlayhead(e.currentTarget.currentTime * 1000)} />
              : <p className="muted">Video loading…</p>}
            {timeline && (
              <div className="timeline" style={{ marginTop: 10 }}
                   onClick={(e) => {
                     const r = e.currentTarget.getBoundingClientRect();
                     seek(((e.clientX - r.left) / r.width) * duration);
                   }}>
                {timeline.segments.map((s) => (
                  <div key={s.idx} className="seg" style={{
                    left: `${(s.start_ms / duration) * 100}%`,
                    width: `${((s.end_ms - s.start_ms) / duration) * 100}%`,
                    background: s.idx % 2 ? "var(--accent)" : "var(--muted)",
                  }} title={`segment ${s.idx} (${s.transition_type})`} />
                ))}
                {timeline.actions.map((a) => (
                  <div key={a.id} className="marker" title={`${a.action_type} ${a.target_desc}`}
                       style={{ left: `${(a.t_start_ms / duration) * 100}%`,
                                background: confColor(a.confidence) }}
                       onClick={(e) => { e.stopPropagation(); seek(a.t_start_ms); }} />
                ))}
                {body.bug_manifestation && (
                  <div className="bugmark"
                       style={{ left: `${(body.bug_manifestation.t_ms / duration) * 100}%` }}
                       title={`bug manifestation ${fmtTs(body.bug_manifestation.t_ms)}`} />
                )}
                <div className="playhead" style={{ left: `${(playhead / duration) * 100}%` }} />
              </div>
            )}
          </div>

          <div className="card">
            <h2>Observed result</h2>
            <textarea rows={3} value={body.observed_result.text}
                      onChange={(e) => { setBody({ ...body,
                        observed_result: { ...body.observed_result, text: e.target.value } });
                        setDirty(true); }} />
            <h2 style={{ marginTop: 14 }}>Expected result</h2>
            <textarea rows={3}
                      placeholder="(not stated — add only if you can verify what should happen)"
                      value={body.expected_result?.text ?? ""}
                      onChange={(e) => { setBody({ ...body,
                        expected_result: e.target.value
                          ? { text: e.target.value, basis: "ui_text", confidence: 1.0 }
                          : null });
                        setDirty(true); }} />
            {body.ambiguity_notes.length > 0 && (
              <>
                <h2 style={{ marginTop: 14 }}>Ambiguities</h2>
                <ul className="muted small">
                  {body.ambiguity_notes.map((n, i) => <li key={i}>{n}</li>)}
                </ul>
              </>
            )}
            {body.omitted_activity.length > 0 && (
              <>
                <h2 style={{ marginTop: 14 }}>Omitted activity</h2>
                <ul className="muted small">
                  {body.omitted_activity.map((o, i) => (
                    <li key={i}>{fmtTs(o.t_start_ms)}–{fmtTs(o.t_end_ms)}: {o.reason}</li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </div>

        {/* ── steps editor pane ── */}
        <div>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
              <h2 style={{ margin: 0 }}>Steps to reproduce</h2>
              <div className="row" style={{ gap: 8 }}>
                <button onClick={save} disabled={!dirty}>Save revision</button>
                <button className="secondary" onClick={approve}>Approve</button>
              </div>
            </div>
            {body.steps.map((step) => (
              <div key={step.key} className={`step-card ${step.confidence < 0.6 ? "lowconf" : ""}`}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span className="small muted">
                    {step.index}. {ACTION_ICON[step.action_type] ?? "•"} {step.action_type}
                    {" · "}<span className="badge">{step.grounding}</span>
                    {step.evidence[0] && (
                      <a onClick={() => seek(step.evidence[0].t_start_ms)}
                         style={{ marginLeft: 6, cursor: "pointer" }}>
                        {fmtTs(step.evidence[0].t_start_ms)}
                      </a>
                    )}
                  </span>
                  <span className="small" style={{ color: confColor(step.confidence) }}>
                    {step.confidence.toFixed(2)}
                  </span>
                </div>
                <textarea rows={2} value={step.text} style={{ marginTop: 6 }}
                          onChange={(e) => patchStep(step.key, { text: e.target.value })} />
                {step.uncertainty_note && (
                  <p className="small" style={{ color: "var(--warn)", margin: "4px 0 0" }}>
                    ⚠ {step.uncertainty_note}
                  </p>
                )}
                <div className="row" style={{ marginTop: 6, gap: 6 }}>
                  {step.evidence.map((ev, i) => {
                    const frame = frameById.get(ev.frame_id);
                    return frame
                      ? <img key={i} src={frame.url} className="thumb" alt="evidence"
                             onClick={() => seek(ev.t_start_ms)} />
                      : null;
                  })}
                  <span className="spacer" style={{ flex: 1 }} />
                  <button className="secondary small" onClick={() => mergeWithNext(step.key)}>
                    merge ↓
                  </button>
                  <button className="danger small" onClick={() => deleteStep(step.key)}>
                    delete
                  </button>
                </div>
              </div>
            ))}
          </div>

          <div className="card">
            <h2>Export</h2>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button className="secondary" onClick={() => doExport("text")}>Text file</button>
              <button className="secondary" onClick={() => doExport("markdown")}>Markdown</button>
              <button className="secondary" onClick={() => doExport("json")}>JSON</button>
              <button className="secondary" onClick={() => doExport("github")}>GitHub issue</button>
              <button onClick={() => doExport("ado")}>Azure DevOps Bug</button>
            </div>
            {exportOut && (
              <textarea rows={10} readOnly value={exportOut} style={{ marginTop: 10 }} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
