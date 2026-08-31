import { useEffect, useRef, useState } from "react";
import { API_BASE, apiFetch } from "./api";
import "./App.css";

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
  const [notice, setNotice] = useState(null);

  const sessionIdRef = useRef(null);
  const recordingRef = useRef(false);
  const streamRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const uploadChainRef = useRef(Promise.resolve());
  const chunkDoneRef = useRef(Promise.resolve());
  const esRef = useRef(null);

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
        case "snapshot":
          setTranscript(m.transcript || "");
          setQuestions(Array.isArray(m.questions) ? m.questions : []);
          setSummary(m.summary || null);
          setError(m.error || null);
          if (m.status) setStatus(m.status);
          break;
        case "transcript":
          setTranscript(m.full);
          setNotice(null);
          break;
        case "questions":
          setQuestions((q) => [...q, ...m.items]);
          break;
        case "status":
          setStatus(m.status);
          break;
        case "warning":
          setNotice(m.message);
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
    es.onerror = () => {};
  }

  async function uploadChunk(blob) {
    try {
      const fd = new FormData();
      fd.append("file", blob, "chunk.webm");
      await apiFetch(`/upload_chunk/${sessionIdRef.current}`, {
        method: "POST",
        body: fd,
      });
    } catch {
      setNotice("A chunk failed to upload.");
    }
  }

  function enqueueUpload(blob) {
    const done = uploadChainRef.current.then(() => uploadChunk(blob));
    uploadChainRef.current = done.catch(() => {});
    return done;
  }

  function recordOneChunk() {
    const stream = streamRef.current;
    if (!stream) return;

    const mr = new MediaRecorder(stream, { mimeType: pickMime() });
    mediaRecorderRef.current = mr;
    const parts = [];

    let markDone;
    chunkDoneRef.current = new Promise((resolve) => {
      markDone = resolve;
    });

    mr.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) parts.push(e.data);
    };
    mr.onstop = () => {
      const blob = new Blob(parts, { type: "audio/webm" });
      if (blob.size > 0) enqueueUpload(blob);
      markDone();
      if (recordingRef.current) recordOneChunk();
    };
    mr.onerror = () => {
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
    setNotice(null);
    setTranscript("");
    setQuestions([]);
    setSummary(null);
    uploadChainRef.current = Promise.resolve();
    chunkDoneRef.current = Promise.resolve();

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
    if (mr && mr.state === "recording") mr.stop();

    await chunkDoneRef.current;
    stopStream();
    await uploadChainRef.current;

    try {
      const end = await apiFetch(`/end_lecture/${sessionIdRef.current}`, { method: "POST" });
      if (!end.ok) throw new Error("end_lecture failed: " + (await end.text()));
    } catch (err) {
      setError(String(err));
      setStatus("error");
    }
  }

  function cancel() {
    recordingRef.current = false;
    const mr = mediaRecorderRef.current;
    if (mr && mr.state !== "inactive") mr.stop();
    esRef.current?.close();
    stopStream();
    uploadChainRef.current = Promise.resolve();
    chunkDoneRef.current = Promise.resolve();
    setSessionId(null);
    sessionIdRef.current = null;
    setStatus("idle");
    setError(null);
    setNotice(null);
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
        {notice && <span className="muted"> — {notice}</span>}
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
