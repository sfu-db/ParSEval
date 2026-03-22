"use client";

import { useEffect, useMemo, useState } from "react";

import { type HeatmapSelection } from "@/components/modelrun/SettingConfusionHeatmap";
import { EvalRecordAPI } from "@/lib/api/evalRecord";
import { getSettingExplanation, getSettingsForRows, type SettingKey, type SettingMeta } from "@/lib/settings";
import { type EvalRecord, type QueryResult, type RelaxedEquivalenceRecord, type WitenessDatabase } from "@/lib/types";

const C = {
    panel: "#ffffff",
    border: "#dde1e7",
    text: "#1c2128",
    muted: "#6e7781",
    faint: "#eef1f4",
    equiv: "#1a7f37",
    nequiv: "#cf222e",
    mixed: "#9a6700",
    blue: "#1d4ed8",
};

const F = {
    display: "'Syne', sans-serif",
    mono: "'JetBrains Mono', monospace",
    body: "'DM Sans', sans-serif",
};

const PER_PAGE = 15;

type Verdict = "equiv" | "diff" | "mixed" | "empty";
type SortDir = "asc" | "desc";
type SortKey = "question_id" | "db_id" | "verdict" | SettingKey;
type AnnotatedRow = EvalRecord & { verdict: Verdict };

interface Props {
    results: EvalRecord[];
    activeSettings?: Set<SettingKey>;
    selectedHeatmapCell?: HeatmapSelection | null;
    onClearHeatmapSelection?: () => void;
}

const VERDICT_META: Record<Verdict, { label: string; color: string }> = {
    equiv: { label: "All Equiv", color: C.equiv },
    diff: { label: "All Diff", color: C.nequiv },
    mixed: { label: "Mixed", color: C.mixed },
    empty: { label: "No Data", color: C.muted },
};

const VERDICT_SORT_ORDER: Record<Verdict, number> = {
    mixed: 0,
    diff: 1,
    equiv: 2,
    empty: 3,
};

function verdictFor(result: EvalRecord, activeSKeys: SettingKey[]): Verdict {
    const vals = activeSKeys
        .map((key) => result.labels[key])
        .filter((value) => value !== null && value !== undefined) as boolean[];
    if (!vals.length) return "empty";
    if (vals.every(Boolean)) return "equiv";
    if (vals.every((value) => value === false)) return "diff";
    return "mixed";
}

function matchesHeatmapSelection(result: EvalRecord, selection: HeatmapSelection | null | undefined) {
    if (!selection) return true;

    const left = result.labels[selection.rowKey];
    const right = result.labels[selection.colKey];
    if (left === undefined || right === undefined) return false;

    if (selection.mode === "joint_equiv") {
        return left === true && right === true;
    }

    return left !== right;
}

function ellipsize(value: string | undefined, max = 96) {
    if (!value) return "-";
    const compact = value.replace(/\s+/g, " ").trim();
    return compact.length > max ? `${compact.slice(0, max - 1)}...` : compact;
}

function VerdictBadge({ verdict }: { verdict: Verdict }) {
    const { label, color } = VERDICT_META[verdict];
    return (
        <span style={{
            background: `${color}18`,
            color,
            border: `1px solid ${color}44`,
            borderRadius: 4,
            padding: "2px 8px",
            fontSize: 11,
            fontWeight: 700,
            fontFamily: F.mono,
            whiteSpace: "nowrap",
        }}>
            {label}
        </span>
    );
}

function SettingCell({ value }: { value: boolean | null | undefined }) {
    if (value === true) return <span style={{ color: C.equiv, fontSize: 15, fontWeight: 700 }}>✓</span>;
    if (value === false) return <span style={{ color: C.nequiv, fontSize: 15, fontWeight: 700 }}>✗</span>;
    return <span style={{ color: C.muted, fontSize: 13 }}>-</span>;
}

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
    if (!active) return <span style={{ color: C.border, fontSize: 10 }}>↕</span>;
    return <span style={{ color: C.blue, fontSize: 10 }}>{dir === "asc" ? "↑" : "↓"}</span>;
}

function FilterChip({ label, active, color, count, onClick }: { label: string; active: boolean; color: string; count: number; onClick: () => void }) {
    return (
        <button
            type="button"
            onClick={onClick}
            style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 12px",
                background: active ? `${color}18` : "transparent",
                color: active ? color : C.muted,
                border: `1.5px solid ${active ? color : C.border}`,
                borderRadius: 20,
                fontSize: 11,
                fontWeight: 700,
                fontFamily: F.body,
                cursor: "pointer",
                transition: "all 0.12s",
                whiteSpace: "nowrap",
            }}
        >
            {label}
            <span style={{
                background: active ? `${color}30` : C.faint,
                color: active ? color : C.muted,
                borderRadius: 10,
                padding: "0 6px",
                fontSize: 10,
                fontFamily: F.mono,
            }}>
                {count}
            </span>
        </button>
    );
}

function Pagination({ page, pages, total, perPage, onPage }: { page: number; pages: number; total: number; perPage: number; onPage: (page: number) => void }) {
    const from = (page - 1) * perPage + 1;
    const to = Math.min(page * perPage, total);
    return (
        <div style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "10px 16px",
            borderTop: `1px solid ${C.border}`,
            background: C.faint,
            flexWrap: "wrap",
        }}>
            <span style={{ fontFamily: F.body, fontSize: 12, color: C.muted, marginRight: "auto" }}>
                {from}-{to} of {total} pairs
            </span>
            <button type="button" onClick={() => onPage(page - 1)} disabled={page === 1} style={{ background: "transparent", border: `1.5px solid ${C.border}`, borderRadius: 6, padding: "3px 10px", fontSize: 12, cursor: page === 1 ? "not-allowed" : "pointer", color: page === 1 ? C.muted : C.text, fontFamily: F.mono, opacity: page === 1 ? 0.4 : 1 }}>←</button>
            {Array.from({ length: Math.min(pages, 7) }, (_, index) => {
                const target = pages <= 7 ? index + 1 : page <= 4 ? index + 1 : page >= pages - 3 ? pages - 6 + index : page - 3 + index;
                return (
                    <button key={target} type="button" onClick={() => onPage(target)} style={{ background: page === target ? C.blue : "transparent", color: page === target ? "#fff" : C.muted, border: `1.5px solid ${page === target ? C.blue : C.border}`, borderRadius: 6, padding: "3px 9px", fontSize: 12, cursor: "pointer", fontFamily: F.mono, fontWeight: 700 }}>
                        {target}
                    </button>
                );
            })}
            <button type="button" onClick={() => onPage(page + 1)} disabled={page === pages} style={{ background: "transparent", border: `1.5px solid ${C.border}`, borderRadius: 6, padding: "3px 10px", fontSize: 12, cursor: page === pages ? "not-allowed" : "pointer", color: page === pages ? C.muted : C.text, fontFamily: F.mono, opacity: page === pages ? 0.4 : 1 }}>→</button>
        </div>
    );
}

function formatCellValue(value: unknown) {
    if (value === null || value === undefined) return "-";
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    return JSON.stringify(value);
}

function ResultTable({ columns, rows, emptyMessage }: { columns?: string[]; rows?: unknown[][]; emptyMessage: string }) {
    const safeRows = rows ?? [];
    const derivedColumns = columns?.length
        ? columns
        : safeRows[0]?.map((_, index) => `col_${index + 1}`) ?? [];

    if (!safeRows.length) {
        return <div style={{ border: `1px solid ${C.border}`, borderRadius: 8, padding: "12px 14px", background: C.faint, color: C.muted, fontSize: 12, fontFamily: F.body }}>{emptyMessage}</div>;
    }

    return (
        <div style={{ border: `1px solid ${C.border}`, borderRadius: 8, overflowX: "auto", background: C.panel }}>
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: derivedColumns.length ? derivedColumns.length * 120 : 240 }}>
                <thead style={{ background: C.faint }}>
                    <tr>
                        {derivedColumns.map((column, index) => (
                            <th key={`${column}-${index}`} style={{ padding: "8px 10px", borderBottom: `1px solid ${C.border}`, textAlign: "left", fontFamily: F.body, fontSize: 10, fontWeight: 700, color: C.muted, letterSpacing: "0.06em", textTransform: "uppercase", whiteSpace: "nowrap" }}>
                                {column}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {safeRows.map((row, rowIndex) => (
                        <tr key={`row-${rowIndex}`} style={{ borderBottom: rowIndex === safeRows.length - 1 ? "none" : `1px solid ${C.border}` }}>
                            {derivedColumns.map((_, columnIndex) => (
                                <td key={`cell-${rowIndex}-${columnIndex}`} style={{ padding: "8px 10px", fontFamily: F.mono, fontSize: 12, color: C.text, whiteSpace: "pre-wrap", wordBreak: "break-word", verticalAlign: "top" }}>
                                    {formatCellValue(row?.[columnIndex])}
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function QueryResultCard({ title, result, accent }: { title: string; result: QueryResult | undefined; accent: string }) {
    return (
        <div style={{ border: `1px solid ${C.border}`, borderRadius: 12, overflow: "hidden", background: C.panel }}>
            <div style={{ padding: "10px 14px", borderBottom: `1px solid ${C.border}`, background: `${accent}10`, color: accent, fontFamily: F.body, fontSize: 12, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase" }}>
                {title}
            </div>
            <div style={{ padding: 14, display: "grid", gap: 10 }}>
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontFamily: F.mono, fontSize: 11, color: C.muted }}>
                    <span>Dialect: {result?.dialect ?? "-"}</span>
                    <span>Elapsed: {typeof result?.elapsed_time === "number" ? `${result.elapsed_time} ms` : "-"}</span>
                </div>
                {result?.error_msg ? (
                    <div style={{ border: `1px solid ${C.nequiv}44`, background: `${C.nequiv}10`, borderRadius: 8, padding: "10px 12px", color: C.nequiv, fontFamily: F.body, fontSize: 12 }}>
                        {result.error_msg}
                    </div>
                ) : null}
                <div>
                    <div style={{ marginBottom: 6, fontFamily: F.body, fontSize: 11, fontWeight: 700, color: C.muted, letterSpacing: "0.06em", textTransform: "uppercase" }}>Query</div>
                    <pre style={{ margin: 0, background: C.faint, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 12px", whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 12, fontFamily: F.mono, color: C.text }}>{result?.query ?? "-"}</pre>
                </div>
                <div>
                    <div style={{ marginBottom: 6, fontFamily: F.body, fontSize: 11, fontWeight: 700, color: C.muted, letterSpacing: "0.06em", textTransform: "uppercase" }}>Rows</div>
                    <ResultTable columns={result?.columns} rows={result?.rows} emptyMessage="No rows returned." />
                </div>
            </div>
        </div>
    );
}

function WitnessDatabaseCard({ database }: { database: WitenessDatabase | undefined }) {
    if (!database) {
        return <div style={{ color: C.muted, fontSize: 12, fontFamily: F.body }}>No witness database available.</div>;
    }

    return (
        <div style={{ display: "grid", gap: 12 }}>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontFamily: F.mono, fontSize: 11, color: C.muted }}>
                <span>DB: {database.database}</span>
                <span>Path: {database.host_or_path}</span>
            </div>
            {database.tables.map((table) => (
                <div key={table.name} style={{ border: `1px solid ${C.border}`, borderRadius: 12, overflow: "hidden", background: C.panel }}>
                    <div style={{ padding: "10px 14px", borderBottom: `1px solid ${C.border}`, background: C.faint, fontFamily: F.body, fontSize: 12, fontWeight: 700, color: C.text }}>{table.name}</div>
                    <div style={{ padding: "12px 14px" }}>
                        <ResultTable columns={table.columns} rows={table.rows} emptyMessage="No witness rows available." />
                    </div>
                </div>
            ))}
        </div>
    );
}

function PairDetailModal({ row, visibleSettings, onClose }: { row: AnnotatedRow; visibleSettings: SettingMeta[]; onClose: () => void }) {
    const [detail, setDetail] = useState<RelaxedEquivalenceRecord | null>(null);
    const [loadingDetail, setLoadingDetail] = useState(false);
    const [detailError, setDetailError] = useState<string | null>(null);

    useEffect(() => {
        let active = true;

        async function loadDetail() {
            try {
                setLoadingDetail(true);
                setDetailError(null);
                const data = await EvalRecordAPI.getRelaxedEquivalenceForQuestionByModelRunId(row.runId, row.dataset, row.db_id, row.question_id);
                if (active) setDetail(data);
            } catch (error) {
                if (active) {
                    console.error(error);
                    setDetail(null);
                    setDetailError("Witness details are not available for this query pair.");
                }
            } finally {
                if (active) setLoadingDetail(false);
            }
        }

        loadDetail();
        return () => {
            active = false;
        };
    }, [row]);

    const counterExamples = visibleSettings.filter((setting) => row.labels[setting.key] === false);
    const equivSettings = visibleSettings.filter((setting) => row.labels[setting.key] === true);
    const naSettings = visibleSettings.filter((setting) => row.labels[setting.key] === null || row.labels[setting.key] === undefined);
    const witnessRecord = detail?.counternexample?.[0];

    return (
        <div onClick={(event) => { if (event.target === event.currentTarget) onClose(); }} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
            <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 16, width: "min(1120px, 96vw)", maxHeight: "90vh", overflowY: "auto", boxShadow: "0 16px 48px rgba(0,0,0,0.18)" }}>
                <div style={{ padding: "18px 24px 14px", borderBottom: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center", position: "sticky", top: 0, background: C.panel, zIndex: 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                        <span style={{ fontFamily: F.display, fontSize: 17, fontWeight: 800, color: C.text }}>Pair Detail</span>
                        <span style={{ background: C.faint, color: C.muted, borderRadius: 4, padding: "2px 8px", fontSize: 11, fontFamily: F.mono }}>Q{row.question_id}</span>
                        <span style={{ background: C.faint, color: C.muted, borderRadius: 4, padding: "2px 8px", fontSize: 11, fontFamily: F.mono }}>{row.db_id}</span>
                        <VerdictBadge verdict={row.verdict} />
                    </div>
                    <button type="button" onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: C.muted, fontSize: 20, lineHeight: 1, padding: "0 4px" }}>✕</button>
                </div>

                <div style={{ padding: "20px 24px", display: "flex", flexDirection: "column", gap: 20 }}>
                    <div>
                        <div style={{ marginBottom: 6, fontFamily: F.body, fontSize: 11, fontWeight: 700, color: C.muted, letterSpacing: "0.08em", textTransform: "uppercase" }}>Question</div>
                        <div style={{ border: `1px solid ${C.border}`, borderRadius: 10, padding: "14px 16px", background: C.faint, fontFamily: F.body, fontSize: 14, color: C.text, lineHeight: 1.6 }}>{row.question}</div>
                    </div>

                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
                        {([
                            ["Gold SQL", row.gold, "#0550ae", "#eff6ff"],
                            ["Predicted SQL", row.pred, "#6d28d9", "#f5f3ff"],
                        ] as [string, string | undefined, string, string][]).map(([title, sql, textColor, background]) => (
                            <div key={title}>
                                <div style={{ marginBottom: 8, fontFamily: F.body, fontSize: 10, fontWeight: 700, color: C.muted, letterSpacing: "0.08em", textTransform: "uppercase" }}>{title}</div>
                                <pre style={{ margin: 0, background, border: `1px solid ${textColor}22`, borderRadius: 10, padding: "14px 16px", color: textColor, fontSize: 12, fontFamily: F.mono, whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.65, minHeight: 100 }}>{sql ?? "-"}</pre>
                            </div>
                        ))}
                    </div>

                    <div>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                            <h4 style={{ margin: 0, fontFamily: F.display, fontSize: 14, fontWeight: 700, color: C.text }}>Counter-Examples</h4>
                            <span style={{ background: `${C.nequiv}18`, color: C.nequiv, border: `1px solid ${C.nequiv}44`, borderRadius: 10, padding: "1px 8px", fontSize: 11, fontWeight: 700, fontFamily: F.mono }}>{counterExamples.length} setting{counterExamples.length !== 1 ? "s" : ""}</span>
                        </div>
                        {counterExamples.length === 0 ? (
                            <div style={{ background: `${C.equiv}0f`, border: `1px solid ${C.equiv}33`, borderRadius: 10, padding: "14px 18px", display: "flex", alignItems: "center", gap: 10 }}>
                                <span style={{ fontSize: 18 }}>✓</span>
                                <span style={{ fontFamily: F.body, fontSize: 13, color: C.equiv, fontWeight: 600 }}>No counter-examples. The predicted SQL is equivalent under all evaluated settings.</span>
                            </div>
                        ) : (
                            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                                {counterExamples.map((setting) => (
                                    <div key={setting.key} style={{ background: `${C.nequiv}08`, border: `1px solid ${C.nequiv}30`, borderLeft: `4px solid ${setting.color}`, borderRadius: 10, padding: "14px 18px" }}>
                                        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                                            <div style={{ width: 10, height: 10, borderRadius: "50%", background: setting.color, flexShrink: 0 }} />
                                            <span style={{ fontFamily: F.body, fontSize: 13, fontWeight: 700, color: C.text }}>{setting.label}</span>
                                            <span style={{ background: `${C.nequiv}18`, color: C.nequiv, border: `1px solid ${C.nequiv}44`, borderRadius: 4, padding: "1px 7px", fontSize: 10, fontWeight: 700, fontFamily: F.mono }}>≠ not equivalent</span>
                                        </div>
                                        <p style={{ margin: 0, fontFamily: F.body, fontSize: 12, color: C.muted, lineHeight: 1.6 }}>{getSettingExplanation(setting.key)}</p>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    <div>
                        <h4 style={{ margin: "0 0 10px", fontFamily: F.display, fontSize: 14, fontWeight: 700, color: C.text }}>All Settings</h4>
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
                            {visibleSettings.map((setting) => {
                                const value = row.labels[setting.key];
                                const accent = value === true ? C.equiv : value === false ? C.nequiv : C.muted;
                                return (
                                    <div key={setting.key} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: C.faint, border: `1px solid ${C.border}`, borderLeft: `4px solid ${setting.color}`, borderRadius: 8, padding: "10px 14px" }}>
                                        <span style={{ fontFamily: F.body, fontSize: 12, fontWeight: 600, color: C.text }}>{setting.label}</span>
                                        <span style={{ fontFamily: F.mono, fontSize: 13, fontWeight: 700, color: accent }}>{value === true ? "✓" : value === false ? "✗" : "-"}</span>
                                    </div>
                                );
                            })}
                        </div>
                    </div>

                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        {[
                            { items: equivSettings, col: C.equiv, label: "equivalent" },
                            { items: counterExamples, col: C.nequiv, label: "not equivalent" },
                            { items: naSettings, col: C.muted, label: "not evaluated" },
                        ].filter((item) => item.items.length > 0).map(({ items, col, label }) => (
                            <div key={label} style={{ background: `${col}12`, border: `1px solid ${col}30`, borderRadius: 8, padding: "6px 14px", display: "flex", gap: 8, alignItems: "center" }}>
                                <span style={{ fontFamily: F.mono, fontSize: 16, fontWeight: 800, color: col }}>{items.length}</span>
                                <span style={{ fontFamily: F.body, fontSize: 12, color: col, fontWeight: 600 }}>setting{items.length !== 1 ? "s" : ""} {label}</span>
                            </div>
                        ))}
                    </div>

                    <div>
                        <h4 style={{ margin: "0 0 10px", fontFamily: F.display, fontSize: 14, fontWeight: 700, color: C.text }}>Witness Database and Query Results</h4>
                        {loadingDetail ? <div style={{ color: C.muted, fontSize: 12, fontFamily: F.body }}>Loading witness details...</div> : null}
                        {detailError ? <div style={{ color: C.nequiv, fontSize: 12, fontFamily: F.body }}>{detailError}</div> : null}
                        {!loadingDetail && !detailError ? (
                            <div style={{ display: "grid", gap: 16 }}>
                                {detail?.counternexample?.map((item, index) => (
                                    <div key={`${item.db_id}-${item.question_id}-${index}`} style={{ display: "grid", gap: 14, border: `1px solid ${C.border}`, borderRadius: 14, padding: 16, background: C.faint }}>
                                        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                                            <span style={{ fontFamily: F.body, fontSize: 12, fontWeight: 700, color: C.text }}>Counterexample {index + 1}</span>
                                            <span style={{ fontFamily: F.mono, fontSize: 11, color: C.muted }}>{item.state}</span>
                                            {item.settings.map((setting, settingIndex) => (
                                                <span key={`${setting.db_level}-${setting.query_level}-${settingIndex}`} style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 999, padding: "3px 8px", fontFamily: F.mono, fontSize: 11, color: C.text }}>{setting.db_level}_{setting.query_level}</span>
                                            ))}
                                        </div>
                                        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
                                            <QueryResultCard title="Gold Query Result" result={item.q1_result} accent="#0550ae" />
                                            <QueryResultCard title="Pred Query Result" result={item.q2_result} accent="#6d28d9" />
                                        </div>
                                        <div>
                                            <div style={{ marginBottom: 10, fontFamily: F.body, fontSize: 11, fontWeight: 700, color: C.muted, letterSpacing: "0.06em", textTransform: "uppercase" }}>Witness Database</div>
                                            <WitnessDatabaseCard database={item.witeness_db} />
                                        </div>
                                    </div>
                                )) ?? null}
                                {!detail?.counternexample?.length ? <WitnessDatabaseCard database={witnessRecord?.witeness_db} /> : null}
                            </div>
                        ) : null}
                    </div>
                </div>
            </div>
        </div>
    );
}

export default function PairConsistencyTable({ results, activeSettings, selectedHeatmapCell, onClearHeatmapSelection }: Props) {
    const [activeVerdict, setActiveVerdict] = useState<Verdict | "all">("all");
    const [search, setSearch] = useState("");
    const [sortKey, setSortKey] = useState<SortKey>("verdict");
    const [sortDir, setSortDir] = useState<SortDir>("asc");
    const [page, setPage] = useState(1);
    const [selectedRow, setSelectedRow] = useState<AnnotatedRow | null>(null);

    const settings = useMemo(() => getSettingsForRows(results), [results]);
    const visibleSettings = useMemo(() => activeSettings ? settings.filter((setting) => activeSettings.has(setting.key)) : settings, [activeSettings, settings]);
    const activeSKeys = visibleSettings.map((setting) => setting.key);

    const effectiveHeatmapSelection = useMemo(() => {
        if (!selectedHeatmapCell) return null;
        const available = new Set(visibleSettings.map((setting) => setting.key));
        if (!available.has(selectedHeatmapCell.rowKey) || !available.has(selectedHeatmapCell.colKey)) return null;
        return selectedHeatmapCell;
    }, [selectedHeatmapCell, visibleSettings]);

    const annotated = useMemo<AnnotatedRow[]>(() => results.map((row) => ({ ...row, verdict: verdictFor(row, activeSKeys) })), [results, activeSKeys]);
    const heatmapFiltered = useMemo(() => annotated.filter((row) => matchesHeatmapSelection(row, effectiveHeatmapSelection)), [annotated, effectiveHeatmapSelection]);

    const verdictCounts = useMemo(() => {
        const out: Record<string, number> = { all: heatmapFiltered.length };
        for (const verdict of ["equiv", "diff", "mixed", "empty"] as Verdict[]) {
            out[verdict] = heatmapFiltered.filter((row) => row.verdict === verdict).length;
        }
        return out;
    }, [heatmapFiltered]);

    const processed = useMemo(() => {
        const query = search.toLowerCase().trim();
        const rows = heatmapFiltered.filter((row) => {
            if (activeVerdict !== "all" && row.verdict !== activeVerdict) return false;
            if (!query) return true;
            const haystack = [row.question_id, row.db_id, row.question, row.gold, row.pred].join(" ").toLowerCase();
            return haystack.includes(query);
        });

        rows.sort((left, right) => {
            let a: string | number;
            let b: string | number;
            if (sortKey === "verdict") {
                a = VERDICT_SORT_ORDER[left.verdict];
                b = VERDICT_SORT_ORDER[right.verdict];
            } else if (sortKey === "question_id") {
                a = left.question_id;
                b = right.question_id;
            } else if (sortKey === "db_id") {
                a = left.db_id;
                b = right.db_id;
            } else {
                const rank = (value: boolean | null | undefined) => value === true ? 2 : value === false ? 1 : 0;
                a = rank(left.labels[sortKey]);
                b = rank(right.labels[sortKey]);
            }
            if (a < b) return sortDir === "asc" ? -1 : 1;
            if (a > b) return sortDir === "asc" ? 1 : -1;
            return 0;
        });

        return rows;
    }, [heatmapFiltered, activeVerdict, search, sortKey, sortDir]);

    const pages = Math.max(1, Math.ceil(processed.length / PER_PAGE));
    const safePage = Math.min(page, pages);
    const paged = processed.slice((safePage - 1) * PER_PAGE, safePage * PER_PAGE);

    const toggleSort = (key: SortKey) => {
        if (sortKey === key) setSortDir((current) => current === "asc" ? "desc" : "asc");
        else {
            setSortKey(key);
            setSortDir("asc");
        }
        setPage(1);
    };

    const renderColHeader = ({ label, sk, align = "left" }: { label: string; sk: SortKey; align?: "left" | "center" }) => (
        <th onClick={() => toggleSort(sk)} style={{ padding: "10px 14px", textAlign: align, cursor: "pointer", fontFamily: F.body, fontSize: 10, fontWeight: 700, color: sortKey === sk ? C.blue : C.muted, letterSpacing: "0.07em", textTransform: "uppercase", borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap", userSelect: "none" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                {label}
                <SortIcon active={sortKey === sk} dir={sortDir} />
            </span>
        </th>
    );

    if (!results.length) {
        return <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, padding: "20px 22px", color: C.muted, fontSize: 13, fontFamily: F.body }}>No pairs to display.</div>;
    }

    return (
        <>
            {selectedRow ? <PairDetailModal row={selectedRow} visibleSettings={visibleSettings} onClose={() => setSelectedRow(null)} /> : null}

            <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, overflow: "hidden" }}>
                <div style={{ padding: "20px 22px 0" }}>
                    <h3 style={{ margin: "0 0 4px", fontFamily: F.display, fontSize: 16, color: C.text, fontWeight: 700 }}>Per-Pair Setting Consistency</h3>
                    <p style={{ margin: "0 0 16px", fontSize: 12, color: C.muted, fontFamily: F.body }}>One row per query pair. Click any row to inspect witness data, query results, and counter-examples.</p>

                    {effectiveHeatmapSelection ? (
                        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 12, padding: "10px 12px", background: "#eff6ff", border: "1px solid #bfdbfe", borderRadius: 10, fontFamily: F.body, fontSize: 12, color: C.blue }}>
                            <span style={{ fontWeight: 700 }}>Heatmap filter active</span>
                            <span>Showing query pairs for {effectiveHeatmapSelection.rowKey} × {effectiveHeatmapSelection.colKey} ({effectiveHeatmapSelection.mode === "joint_equiv" ? "both equivalent" : "disagreement"}).</span>
                            {onClearHeatmapSelection ? <button type="button" onClick={onClearHeatmapSelection} style={{ marginLeft: "auto", background: "white", border: "1px solid #bfdbfe", borderRadius: 6, padding: "4px 8px", cursor: "pointer", color: C.blue, fontFamily: F.body, fontSize: 12, fontWeight: 700 }}>Clear</button> : null}
                        </div>
                    ) : null}

                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 14 }}>
                        <span style={{ fontSize: 10, fontWeight: 700, color: C.muted, letterSpacing: "0.07em", textTransform: "uppercase", fontFamily: F.body }}>Verdict</span>
                        <FilterChip label="All" active={activeVerdict === "all"} color={C.muted} count={verdictCounts.all} onClick={() => { setActiveVerdict("all"); setPage(1); }} />
                        {(["mixed", "diff", "equiv"] as Verdict[]).map((verdict) => (
                            <FilterChip key={verdict} label={VERDICT_META[verdict].label} active={activeVerdict === verdict} color={VERDICT_META[verdict].color} count={verdictCounts[verdict] ?? 0} onClick={() => { setActiveVerdict(verdict); setPage(1); }} />
                        ))}
                        <div style={{ position: "relative", marginLeft: "auto" }}>
                            <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: C.muted, fontSize: 13, pointerEvents: "none" }}>⌕</span>
                            <input value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Search question, db, gold, pred..." style={{ background: C.panel, border: `1.5px solid ${C.border}`, borderRadius: 8, padding: "6px 32px 6px 30px", fontFamily: F.mono, fontSize: 12, color: C.text, outline: "none", width: 320 }} onFocus={(event) => { event.target.style.borderColor = C.blue; }} onBlur={(event) => { event.target.style.borderColor = C.border; }} />
                            {search ? <button type="button" onClick={() => { setSearch(""); setPage(1); }} style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: C.muted, fontSize: 13, lineHeight: 1 }}>✕</button> : null}
                        </div>
                    </div>
                </div>

                <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse" }}>
                        <thead style={{ background: C.faint }}>
                            <tr>
                                {renderColHeader({ label: "Q #", sk: "question_id" })}
                                {renderColHeader({ label: "DB", sk: "db_id" })}
                                <th style={{ padding: "10px 14px", textAlign: "left", fontFamily: F.body, fontSize: 10, fontWeight: 700, color: C.muted, letterSpacing: "0.07em", textTransform: "uppercase", borderBottom: `1px solid ${C.border}`, minWidth: 280 }}>Question</th>
                                <th style={{ padding: "10px 14px", textAlign: "left", fontFamily: F.body, fontSize: 10, fontWeight: 700, color: C.muted, letterSpacing: "0.07em", textTransform: "uppercase", borderBottom: `1px solid ${C.border}`, minWidth: 240 }}>Pred SQL</th>
                                {visibleSettings.map((setting) => (
                                    <th key={setting.key} onClick={() => toggleSort(setting.key)} style={{ padding: "10px 12px", textAlign: "center", cursor: "pointer", fontFamily: F.body, fontSize: 10, fontWeight: 700, color: sortKey === setting.key ? setting.color : C.muted, letterSpacing: "0.05em", borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap", userSelect: "none" }}>
                                        <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
                                            <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: setting.color, flexShrink: 0 }} />
                                            {setting.short}
                                            <SortIcon active={sortKey === setting.key} dir={sortDir} />
                                        </span>
                                    </th>
                                ))}
                                {renderColHeader({ label: "Verdict", sk: "verdict", align: "center" })}
                            </tr>
                        </thead>
                        <tbody>
                            {!paged.length ? (
                                <tr>
                                    <td colSpan={5 + visibleSettings.length} style={{ padding: "32px", textAlign: "center", color: C.muted, fontFamily: F.body, fontSize: 13 }}>No pairs match the current filters{search ? ` for "${search}"` : ""}{effectiveHeatmapSelection ? " and selected heatmap cell" : ""}.</td>
                                </tr>
                            ) : paged.map((row) => {
                                const borderColor = VERDICT_META[row.verdict].color;
                                return (
                                    <tr key={`${row.runId}-${row.db_id}-${row.question_id}`} onClick={() => setSelectedRow(row)} style={{ borderBottom: `1px solid ${C.border}`, borderLeft: `3px solid ${borderColor}`, cursor: "pointer", transition: "background 0.1s" }} onMouseEnter={(event) => { event.currentTarget.style.background = C.faint; }} onMouseLeave={(event) => { event.currentTarget.style.background = "transparent"; }}>
                                        <td style={{ padding: "10px 14px", fontFamily: F.mono, fontSize: 12, color: C.muted, whiteSpace: "nowrap" }}>Q{row.question_id}</td>
                                        <td style={{ padding: "10px 14px", fontFamily: F.mono, fontSize: 11, color: C.muted, whiteSpace: "nowrap" }}>{row.db_id}</td>
                                        <td style={{ padding: "10px 14px", fontFamily: F.body, fontSize: 12, color: C.text, lineHeight: 1.5 }} title={row.question}>{ellipsize(row.question, 140)}</td>
                                        <td style={{ padding: "10px 14px", fontFamily: F.mono, fontSize: 11, color: C.muted, lineHeight: 1.5 }} title={row.pred}>{ellipsize(row.pred, 120)}</td>
                                        {visibleSettings.map((setting) => (
                                            <td key={setting.key} style={{ padding: "10px 12px", textAlign: "center" }}>
                                                <SettingCell value={row.labels[setting.key]} />
                                            </td>
                                        ))}
                                        <td style={{ padding: "10px 14px", textAlign: "center" }}><VerdictBadge verdict={row.verdict} /></td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>

                {processed.length > PER_PAGE ? <Pagination page={safePage} pages={pages} total={processed.length} perPage={PER_PAGE} onPage={setPage} /> : null}
            </div>
        </>
    );
}
