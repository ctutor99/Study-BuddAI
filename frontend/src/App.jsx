// src/App.jsx
import React, { useRef, useState } from "react";
import "./App.css";

/**
 * Minimal frontend for Study BuddAI - Post-lecture mode.
 * - Start recording (browser mic)
 * - Stop & upload (single assembled blob)
 * - Poll results until status=done
 *
 * Note: endpoints match the backend at /start_lecture, /upload_chunk/{id}, /end_lecture/{id}
 */

export default function App() {
  const [sessionId, setSessionId] = useState(null);
  const [status, setStatus] = useState("idle");
  const [transcript, setTranscript] = useState("");
  const [summary, setSummary] = useState("");
  const [engagement, setEngagement] = useState(null);
  const [profQuestions, setProfQuestions] = useState(null);
  const [error, setError] = useState(null);

  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);
  const pollRef = useRef(null);

  async function startLecture() {
    setError(null);
    try {
      const resp = await fetch("/start_lecture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "Lecture " + new Date().toISOString() }),
      });
      const j = await resp.json();
      if (!j.session_id) throw new Error("start_lecture failed");
      setSessionId(j.session_id);

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mime = MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "audio/webm";
      const mr = new MediaRecorder(stream, { mimeType: mime });

      mediaRecorderRef.current = mr;
      chunksRef.current = [];

      mr.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          chunksRef.current.push(e.data);
        }
      };

      mr.onstart = () => setStatus("recording");
      mr.onerror = (ev) => {
        console.error("MediaRecorder error", ev);
        setError("Recording error");
        setStatus("error");
      };

      // timeslice ensures dataavailable events arrive regularly
      mr.start(1000);
    } catch (err) {
      console.error(err);
      setError(String(err));
      setStatus("error");
    }
  }

  async function stopLectureAndUpload() {
    if (!mediaRecorderRef.current) {
      setError("No active recording");
      return;
    }
    setError(null);
    setStatus("uploading");

    // stop recorder and wait briefly for last chunk
    try {
      const mr = mediaRecorderRef.current;
      await new Promise((resolve) => {
        mr.onstop = () => setTimeout(resolve, 120);
        try {
          if (mr.state !== "inactive") mr.stop();
          else resolve();
        } catch (e) {
          resolve();
        }
      });
    } catch (err) {
      console.warn("stop_wait error", err);
    }

    // stop tracks
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
    }

    const blob = new Blob(chunksRef.current, { type: "audio/webm" });
    if (!blob || blob.size === 0) {
      setError("Recorded file is empty.");
      setStatus("error");
      return;
    }

    try {
      // upload assembled blob as a single chunk (server appends)
      const fd = new FormData();
      fd.append("file", blob, "lecture.webm");
      const up = await fetch(`/upload_chunk/${sessionId}`, { method: "POST", body: fd });
      if (!up.ok) {
        const txt = await up.text();
        throw new Error("upload failed: " + txt);
      }

      // signal processing
      setStatus("processing");
      const end = await fetch(`/end_lecture/${sessionId}`, { method: "POST" });
      if (!end.ok) {
        const txt = await end.text();
        throw new Error("end_lecture failed: " + txt);
      }

      // poll results
      pollRef.current = setInterval(async () => {
        try {
          const r = await fetch(`/results/${sessionId}`);
          if (!r.ok) return;
          const j = await r.json();
          if (j.status === "done") {
            clearInterval(pollRef.current);
            setStatus("done");
            setTranscript(j.transcript || "");
            setSummary(j.summary || "");
            setEngagement(j.engagement_questions || null);
            setProfQuestions(j.prof_questions || null);
          } else if (j.status === "error") {
            clearInterval(pollRef.current);
            setStatus("error");
            setError(j.error || "processing error");
          } else {
            setStatus(j.status || "processing");
          }
        } catch (err) {
          console.error("poll error", err);
        }
      }, 2500);
    } catch (err) {
      console.error(err);
      setError(String(err));
      setStatus("error");
    } finally {
      // clean local recording state
      mediaRecorderRef.current = null;
      chunksRef.current = [];
      streamRef.current = null;
    }
  }

  function cancelRecording() {
    try {
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
        mediaRecorderRef.current.stop();
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
      }
    } catch (e) { /* ignore */ }
    mediaRecorderRef.current = null;
    streamRef.current = null;
    chunksRef.current = [];
    setSessionId(null);
    setStatus("idle");
    setError(null);
  }

  async function downloadFlashcards() {
    if (!sessionId) return;
    const resp = await fetch(`/export_flashcards/${sessionId}`);
    if (!resp.ok) {
      const txt = await resp.text();
      alert("Export failed: " + txt);
      return;
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `flashcards_${sessionId}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  return (
    <div style={{ padding: 24, fontFamily: "system-ui, sans-serif", maxWidth: 900 }}>
      <h1>Study BuddAI — Post-lecture</h1>

      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <button onClick={startLecture} disabled={status === "recording" || status === "uploading" || status === "processing"}>
          Start Lecture
        </button>

        <button onClick={stopLectureAndUpload} disabled={status !== "recording"}>
          Stop & Upload
        </button>

        <button onClick={cancelRecording} disabled={status !== "recording"}>
          Cancel
        </button>

        <button onClick={downloadFlashcards} disabled={status !== "done"}>
          Download Flashcards (CSV)
        </button>
      </div>

      <div style={{ marginBottom: 12 }}>
        <strong>Session:</strong> {sessionId || "none"} <br />
        <strong>Status:</strong> {status} {error ? <span style={{ color: "red" }}> — {error}</span> : null}
      </div>

      <div style={{ marginTop: 12 }}>
        <h3>Summary</h3>
        <pre style={{ whiteSpace: "pre-wrap", background: "#f7f7f7", padding: 10, minHeight: 80 }}>
          {status === "done" ? (summary || "No summary") : "No summary yet"}
        </pre>

        <h3>Transcript</h3>
        <pre style={{ whiteSpace: "pre-wrap", background: "#f7f7f7", padding: 10, maxHeight: 300, overflow: "auto" }}>
          {status === "done" ? (transcript || "No transcript") : "No transcript yet"}
        </pre>

        <h3>Engagement Questions</h3>
        <pre style={{ whiteSpace: "pre-wrap", background: "#fff", padding: 10 }}>
          {engagement ? JSON.stringify(engagement, null, 2) : "No engagement questions yet"}
        </pre>

        <h3>Questions to Ask the Professor</h3>
        <pre style={{ whiteSpace: "pre-wrap", background: "#fff", padding: 10 }}>
          {profQuestions ? JSON.stringify(profQuestions, null, 2) : "No professor questions yet"}
        </pre>
      </div>
    </div>
  );
}