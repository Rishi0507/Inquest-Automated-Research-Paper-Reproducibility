import { useState } from "react";
import { api } from "../api";
import { Empty, PageHead, PaperPicker, useApp } from "../ui";

export default function Report() {
  const { paperId, papers } = useApp();
  const paper = papers.find((p) => p.paper_id === paperId);
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const exportIt = async () => {
    if (!paperId) return;
    setBusy(true); setError(null);
    try {
      const r = await api.dossier(paperId);
      setUrl(`${r.url}?t=${Date.now()}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHead
        title="Evidence dossier"
        sub="The exportable report: every verdict with its rule path, bands, Witness observations, deviations, attribution, specification sweep, cost and the provenance graph."
        right={<>
          <PaperPicker />
          <button className="btn primary" disabled={busy || !paper?.analysis} onClick={exportIt}>{busy ? "Exporting" : "Export PDF"}</button>
          {url && <a className="btn" href={url} download>Download</a>}
        </>}
      />
      {error && <div className="banner warn" style={{ marginBottom: 16 }}>{error}</div>}
      {!paper?.analysis ? (
        <Empty title="Analyse the paper first">The dossier is built from the latest analysis.</Empty>
      ) : url ? (
        <div className="card fade-in" style={{ padding: 0, overflow: "hidden" }}>
          <iframe title="Dossier" src={url} style={{ width: "100%", height: "78vh", border: 0, display: "block", background: "var(--surface-2)" }} />
        </div>
      ) : (
        <Empty title="Ready to export">The PDF opens here once it is built.</Empty>
      )}
    </div>
  );
}
