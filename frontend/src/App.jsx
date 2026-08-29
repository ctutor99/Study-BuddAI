// src/App.jsx
import { useEffect, useRef, useState } from "react";
import { API_BASE, apiFetch } from "./api";
import "./App.css";

/**
 * Study BuddAI — live mode.
 * Record the mic in short standalone chunks and upload each one as it's produced.
 * The backend transcribes every chunk, streams the growing transcript plus
 * questions back over SSE, and only summarizes the whole lecture on "End".
 */

const CHUNK_MS = 5000;
const BUSY = new Set(["recording", "summarizing"]);

function pickMime() {
  if (
    typeof MediaRecorder !== "undefined" &&
    MediaRecorder.isTypeSupported &&
    MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
  ) {
    return "audio/webm;codecs=opus";
  }
  return "audio/webm";
}

export default function App() {
  const [sessionId, setSessionId] = useState(null);
  const [status, setStatus] = useState("idle");
  const [transcript, setTranscript] = useState("");
  const [questions, setQuestions] = useState([]);
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState(null);

  const sessionIdRef = useRef(null);
  const recordingRef = useRef(false);
  const streamRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const lastUploadRef = useRef(Promise.resolve());
  const esRef = useRef(null);

  // Tear down on unmount.
  useEffect(() => {
    return () => {
      recordingRef.current = false;
      esRef.current?.close();
      stopStream();
    };
  }, []);

  function stopStream() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }

  function subscribe(id) {
    const es = new EventSource(`${API_BASE}/events/${id}`);
    esRef.current = es;
    es.onmessage = (ev) => {
      let m;
      try {
        m = JSON.parse(ev.data);
      } catch {
        return;
      }
      switch (m.type) {
        case "transcript":
          setTranscript(m.full);
          break;
        case "questions":
          setQuestions((q) => [...q, ...m.items]);
          break;
        case "status":
          setStatus(m.status);
          break;
        case "summary":
          setSummary(m.summary);
          break;
        case "done":
          setStatus("done");
          es.close();
          break;
        case "error":
          setError(m.error);
          setStatus("error");
          es.close();
          break;
        default:
          break;
      }
    };
    // EventSource reconnects on its own; nothing to do on transient errors.
    es.onerror = () => {};
  }

  async function uploadChunk(blob) {
    try {
      const fd = new FormData();
      fd.append("file", blob, "chunk.webm");
      await apiFetch(`/upload_chunk/${sessionIdRef.current}`, { method: "POST", body: fd });
    } catch (err) {
      console.error("chunk upload failed", err);
    }
  }

  // Record exactly one chunk, then (if still recording) start the next one.
  // Each stop/start cycle yields a self-contained webm the backend can decode.
  function recordOneChunk() {
    const stream = streamRef.current;
    if (!stream) return;

    const mr = new MediaRecorder(stream, { mimeType: pickMime() });
    mediaRecorderRef.current = mr;
    const parts = [];

    mr.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) parts.push(e.data);
    };
    mr.onstop = () => {
      const blob = new Blob(parts, { type: "audio/webm" });
      lastUploadRef.current = blob.size > 0 ? uploadChunk(blob) : Promise.resolve();
      if (recordingRef.current) {
        lastUploadRef.current.finally(() => {
          if (recordingRef.current) recordOneChunk();
        });
      }
    };
    mr.onerror = (ev) => {
      console.error("MediaRecorder error", ev);
      setError("Recording error");
      setStatus("error");
    };

    mr.start();
    setTimeout(() => {
      if (mr.state !== "inactive") mr.stop();
    }, CHUNK_MS);
  }

  async function startLecture() {
    setError(null);
    setTranscript("");
    setQuestions([]);
    setSummary(null);

    try {
      const resp = await apiFetch("/start_lecture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "Lecture " + new Date().toISOString() }),
      });
      const j = await resp.json();
      if (!j.session_id) throw new Error("start_lecture failed");
      setSessionId(j.session_id);
      sessionIdRef.current = j.session_id;

      streamRef.current = await navigator.mediaDevices.getUserMedia({ audio: true });
      subscribe(j.session_id);

      recordingRef.current = true;
      setStatus("recording");
      recordOneChunk();
    } catch (err) {
      console.error(err);
      stopStream();
      recordingRef.current = false;
      setError(String(err));
      setStatus("error");
    }
  }

  async function endLecture() {
    if (!recordingRef.current) return;
    recordingRef.current = false;
    setStatus("summarizing");

    const mr = mediaRecorderRef.current;
    if (mr && mr.state !== "inactive") mr.stop(); // fires onstop -> final upload
    stopStream();

    // Wait for onstop to register the final upload, then for it to finish so the
    // backend has the complete transcript before it summarizes.
    await new Promise((r) => setTimeout(r, 100));
    await lastUploadRef.current;

    try {
      const end = await apiFetch(`/end_lecture/${sessionIdRef.current}`, { method: "POST" });
      if (!end.ok) throw new Error("end_lecture failed: " + (await end.text()));
    } catch (err) {
      console.error(err);
      setError(String(err));
      setStatus("error");
    }
  }

  function cancel() {
    recordingRef.current = false;
    const mr = mediaRecorderRef.current;
    try {
      if (mr && mr.state !== "inactive") mr.stop();
    } catch {
      /* ignore */
    }
    esRef.current?.close();
    stopStream();
    setSessionId(null);
    sessionIdRef.current = null;
    setStatus("idle");
    setError(null);
  }

  const busy = BUSY.has(status);

  return (
    <div className="app">
      <header>
        <h1>Study BuddAI</h1>
        <p className="tagline">
          Live transcript and questions while you record; a full summary when you press End.
        </p>
      </header>

      <div className="controls">
        <button onClick={startLecture} disabled={busy}>
          Start Lecture
        </button>
        <button onClick={endLecture} disabled={status !== "recording"}>
          End
        </button>
        <button className="secondary" onClick={cancel} disabled={status === "idle"}>
          Cancel
        </button>
      </div>

      <div className="statusbar">
        {status === "recording" && <span className="rec-dot" aria-hidden="true" />}
        <span>
          <strong>Status:</strong> {status}
        </span>
        {sessionId && <span className="muted">session {sessionId.slice(0, 8)}</span>}
        {error && <span className="err"> — {error}</span>}
      </div>

      <section>
        <h2>Summary</h2>
        {summary && summary.text ? (
          <>
            <p>{summary.text}</p>
            {summary.bullets?.length > 0 && (
              <ul>
                {summary.bullets.map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p className="muted">
            {status === "summarizing" ? "Summarizing…" : "Generated when you press End."}
          </p>
        )}
      </section>

      <section>
        <h2>Live Transcript</h2>
        <pre className="transcript">
          {transcript || (status === "recording" ? "Listening…" : "Not started.")}
        </pre>
      </section>

      <section>
        <h2>Questions ({questions.length})</h2>
        <QuestionList
          items={questions}
          empty={status === "recording" ? "Waiting for enough transcript…" : "None yet."}
          renderMeta={(q) => q.answer && <div className="answer">{q.answer}</div>}
          renderTag={(q) => q.difficulty}
        />
      </section>
    </div>
  );
}

function QuestionList({ items, empty, renderMeta, renderTag }) {
  if (!Array.isArray(items) || items.length === 0) {
    return <p className="muted">{empty}</p>;
  }
  return (
    <ol className="questions">
      {items.map((q, i) => (
        <li key={i}>
          <div className="q">
            {q.question || JSON.stringify(q)}
            {renderTag && renderTag(q) && <span className="tag">{renderTag(q)}</span>}
          </div>
          {renderMeta && renderMeta(q)}
        </li>
      ))}
    </ol>
  );
}
