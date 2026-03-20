"use client";

import { useMemo, useState } from "react";
import { EvalResult } from "@/lib/types";

// ── Settings ──────────────────────────────────────────────────
const SETTINGS = [
    { key: "no_constraints", label: "No Constraints", short: "No Constr.", color: "#e76f51" },
    { key: "no_null", label: "No Null Values", short: "No Null", color: "#2a9d8f" },
    { key: "positive_only", label: "Positive Only", short: "Pos. Only", color: "#c77dff" },
    { key: "full_constraints", label: "Full Constraints", short: "Full Constr.", color: "#264653" },
    { key: "set_semantics", label: "Set Semantics", short: "Set", color: "#6d6875" },
    { key: "bag_semantics", label: "Bag Semantics", short: "Bag", color: "#457b9d" },
] as const;

type SettingKey = typeof SETTINGS[number]["key"];

// ── Design tokens ─────────────────────────────────────────────
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

// ── Types ─────────────────────────────────────────────────────
type Verdict = "equiv" | "diff" | "mixed" | "empty";
type SortDir = "asc" | "desc";
type SortKey = "question_id" | "db_id" | "verdict" | SettingKey;
type AnnotatedRow = EvalResult & { verdict: Verdict };

interface Props {
    results: EvalResult[];
    activeSettings?: Set<SettingKey>;
}

// ── Helpers ───────────────────────────────────────────────────
const VERDICT_META: Record<Verdict, { label: string; color: string }> = {
    equiv: { label: "All Equiv", color: C.equiv },
    diff: { label: "All Diff", color: C.nequiv },
    mixed: { label: "Mixed", color: C.mixed },
    empty: { label: "No Data", color: C.muted },
};

const VERDICT_SORT_ORDER: Record<Verdict, number> = {
    mixed: 0, diff: 1, equiv: 2, empty: 3,
};

function verdictFor(result: EvalResult, activeSKeys: SettingKey[]): Verdict {
    const vals = activeSKeys
        .map((k) => result.labels[k])
        .filter((v) => v !== null && v !== undefined) as boolean[];
    if (!vals.length) return "empty";
    if (vals.every((v) => v === true)) return "equiv";
    if (vals.every((v) => v === false)) return "diff";
    return "mixed";
}

// ── Atom components ───────────────────────────────────────────
function VerdictBadge({ verdict }: { verdict: Verdict }) {
    const { label, color } = VERDICT_META[verdict];
    return (
        <span style={{
            background: color + "18", color,
            border: `1px solid ${color}44`, borderRadius: 4,
            padding: "2px 8px", fontSize: 11, fontWeight: 700,
            fontFamily: F.mono, whiteSpace: "nowrap",
        }}>
            {label}
        </span>
    );
}

function SettingCell({ value }: { value: boolean | null | undefined }) {
    if (value === true) return <span style={{ color: C.equiv, fontSize: 15, fontWeight: 700 }}>✓</span>;
    if (value === false) return <span style={{ color: C.nequiv, fontSize: 15, fontWeight: 700 }}>✗</span>;
    return <span style={{ color: C.muted, fontSize: 13 }}>—</span>;
}

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
    if (!active) return <span style={{ color: C.border, fontSize: 10 }}>↕</span>;
    return <span style={{ color: C.blue, fontSize: 10 }}>{dir === "asc" ? "↑" : "↓"}</span>;
}

function FilterChip({ label, active, color, count, onClick }: {
    label: string; active: boolean; color: string;
    count: number; onClick: () => void;
}) {
    return (
        <button onClick={onClick} style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "4px 12px",
            background: active ? color + "18" : "transparent",
            color: active ? color : C.muted,
            border: `1.5px solid ${active ? color : C.border}`,
            borderRadius: 20, fontSize: 11, fontWeight: 700,
            fontFamily: F.body, cursor: "pointer",
            transition: "all 0.12s", whiteSpace: "nowrap",
        }}>
            {label}
            <span style={{
                background: active ? color + "30" : C.faint,
                color: active ? color : C.muted,
                borderRadius: 10, padding: "0 6px",
                fontSize: 10, fontFamily: F.mono,
            }}>
                {count}
            </span>
        </button>
    );
}

function Pagination({ page, pages, total, perPage, onPage }: {
    page: number; pages: number; total: number;
    perPage: number; onPage: (p: number) => void;
}) {
    const from = (page - 1) * perPage + 1;
    const to = Math.min(page * perPage, total);
    return (
        <div style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "10px 16px", borderTop: `1px solid ${C.border}`,
            background: C.faint, flexWrap: "wrap",
        }}>
            <span style={{ fontFamily: F.body, fontSize: 12, color: C.muted, marginRight: "auto" }}>
                {from}–{to} of {total} pairs
            </span>
            <button onClick={() => onPage(page - 1)} disabled={page === 1}
                style={{ background: "transparent", border: `1.5px solid ${C.border}`, borderRadius: 6, padding: "3px 10px", fontSize: 12, cursor: page === 1 ? "not-allowed" : "pointer", color: page === 1 ? C.muted : C.text, fontFamily: F.mono, opacity: page === 1 ? 0.4 : 1 }}>
                ←
            </button>
            {Array.from({ length: Math.min(pages, 7) }, (_, i) => {
                let p = pages <= 7 ? i + 1 : page <= 4 ? i + 1 : page >= pages - 3 ? pages - 6 + i : page - 3 + i;
                return (
                    <button key={p} onClick={() => onPage(p)} style={{
                        background: page === p ? C.blue : "transparent",
                        color: page === p ? "#fff" : C.muted,
                        border: `1.5px solid ${page === p ? C.blue : C.border}`,
                        borderRadius: 6, padding: "3px 9px", fontSize: 12,
                        cursor: "pointer", fontFamily: F.mono, fontWeight: 700,
                    }}>{p}</button>
                );
            })}
            <button onClick={() => onPage(page + 1)} disabled={page === pages}
                style={{ background: "transparent", border: `1.5px solid ${C.border}`, borderRadius: 6, padding: "3px 10px", fontSize: 12, cursor: page === pages ? "not-allowed" : "pointer", color: page === pages ? C.muted : C.text, fontFamily: F.mono, opacity: page === pages ? 0.4 : 1 }}>
                →
            </button>
        </div>
    );
}

/* ══════════════════════════════════════════════════════════════
   PAIR DETAIL MODAL
   Shows gold vs pred SQL side-by-side, all setting labels,
   and the counter-examples (settings where pred ≠ equiv).
══════════════════════════════════════════════════════════════ */
function PairDetailModal({
    row,
    visibleSettings,
    onClose,
}: {
    row: AnnotatedRow;
    visibleSettings: typeof SETTINGS[number][];
    onClose: () => void;
}) {
    // Counter-examples = settings where the label is FALSE
    // (i.e. the predicted SQL is NOT equivalent under that setting)
    const counterExamples = visibleSettings.filter(
        (s) => row.labels[s.key] === false
    );

    // Settings where equivalent
    const equivSettings = visibleSettings.filter(
        (s) => row.labels[s.key] === true
    );

    // Settings not evaluated
    const naSettings = visibleSettings.filter(
        (s) => row.labels[s.key] === null || row.labels[s.key] === undefined
    );

    const bc = VERDICT_META[row.verdict].color;

    return (
        <div
            onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
            style={{
                position: "fixed", inset: 0,
                background: "rgba(0,0,0,0.4)",
                zIndex: 1000,
                display: "flex", alignItems: "center", justifyContent: "center",
                padding: 20,
            }}
        >
            <div style={{
                background: C.panel,
                border: `1px solid ${C.border}`,
                borderRadius: 16,
                width: "min(860px, 95vw)",
                maxHeight: "90vh",
                overflowY: "auto",
                boxShadow: "0 16px 48px rgba(0,0,0,0.18)",
            }}>

                {/* ── Modal header ── */}
                <div style={{
                    padding: "18px 24px 14px",
                    borderBottom: `1px solid ${C.border}`,
                    display: "flex", justifyContent: "space-between",
                    alignItems: "center", position: "sticky",
                    top: 0, background: C.panel, zIndex: 1,
                }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                        <span style={{ fontFamily: F.display, fontSize: 17, fontWeight: 800, color: C.text }}>
                            Pair Detail
                        </span>
                        {/* Meta badges */}
                        <span style={{ background: "#dbeafe", color: C.blue, border: "1px solid #bfdbfe", borderRadius: 4, padding: "2px 8px", fontSize: 11, fontWeight: 700, fontFamily: F.mono }}>
                            {row.dataset}
                        </span>
                        <span style={{ background: C.faint, color: C.muted, borderRadius: 4, padding: "2px 8px", fontSize: 11, fontFamily: F.mono }}>
                            Q{row.question_id}
                        </span>
                        <span style={{ background: C.faint, color: C.muted, borderRadius: 4, padding: "2px 8px", fontSize: 11, fontFamily: F.mono }}>
                            {row.db_id}
                        </span>
                        <VerdictBadge verdict={row.verdict} />
                    </div>
                    <button onClick={onClose} style={{
                        background: "none", border: "none",
                        cursor: "pointer", color: C.muted,
                        fontSize: 20, lineHeight: 1, padding: "0 4px",
                    }}>✕</button>
                </div>

                <div style={{ padding: "20px 24px", display: "flex", flexDirection: "column", gap: 20 }}>

                    {/* ── SQL comparison ── */}
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
                        {([
                            ["Gold SQL", row.gold, "#0550ae", "#eff6ff"],
                            ["Predicted SQL", row.pred, "#6d28d9", "#f5f3ff"],
                        ] as [string, string | undefined, string, string][]).map(
                            ([title, sql, tc, bg]) => (
                                <div key={title}>
                                    <div style={{
                                        fontFamily: F.body, fontSize: 10,
                                        fontWeight: 700, color: C.muted,
                                        letterSpacing: "0.08em", textTransform: "uppercase",
                                        marginBottom: 8,
                                    }}>
                                        {title}
                                    </div>
                                    <pre style={{
                                        margin: 0, background: bg,
                                        border: `1px solid ${tc}22`,
                                        borderRadius: 10,
                                        padding: "14px 16px",
                                        color: tc, fontSize: 12,
                                        fontFamily: F.mono,
                                        whiteSpace: "pre-wrap",
                                        wordBreak: "break-word",
                                        lineHeight: 1.65,
                                        minHeight: 80,
                                    }}>
                                        {sql ?? "—"}
                                    </pre>
                                </div>
                            )
                        )}
                    </div>

                    {/* ── Counter-examples section ── */}
                    <div>
                        <div style={{
                            display: "flex", alignItems: "center", gap: 8,
                            marginBottom: 12,
                        }}>
                            <h4 style={{
                                margin: 0,
                                fontFamily: F.display, fontSize: 14,
                                fontWeight: 700, color: C.text,
                            }}>
                                Counter-Examples
                            </h4>
                            <span style={{
                                background: C.nequiv + "18",
                                color: C.nequiv,
                                border: `1px solid ${C.nequiv}44`,
                                borderRadius: 10, padding: "1px 8px",
                                fontSize: 11, fontWeight: 700, fontFamily: F.mono,
                            }}>
                                {counterExamples.length} setting{counterExamples.length !== 1 ? "s" : ""}
                            </span>
                        </div>

                        {counterExamples.length === 0 ? (
                            <div style={{
                                background: C.equiv + "0f",
                                border: `1px solid ${C.equiv}33`,
                                borderRadius: 10, padding: "14px 18px",
                                display: "flex", alignItems: "center", gap: 10,
                            }}>
                                <span style={{ fontSize: 18 }}>✓</span>
                                <span style={{ fontFamily: F.body, fontSize: 13, color: C.equiv, fontWeight: 600 }}>
                                    No counter-examples — the predicted SQL is equivalent
                                    under all evaluated settings.
                                </span>
                            </div>
                        ) : (
                            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                                {counterExamples.map((s) => (
                                    <div key={s.key} style={{
                                        background: C.nequiv + "08",
                                        border: `1px solid ${C.nequiv}30`,
                                        borderLeft: `4px solid ${s.color}`,
                                        borderRadius: 10,
                                        padding: "14px 18px",
                                    }}>
                                        {/* Setting header */}
                                        <div style={{
                                            display: "flex", alignItems: "center",
                                            gap: 8, marginBottom: 8,
                                        }}>
                                            <div style={{
                                                width: 10, height: 10,
                                                borderRadius: "50%",
                                                background: s.color, flexShrink: 0,
                                            }} />
                                            <span style={{
                                                fontFamily: F.body, fontSize: 13,
                                                fontWeight: 700, color: C.text,
                                            }}>
                                                {s.label}
                                            </span>
                                            <span style={{
                                                background: C.nequiv + "18",
                                                color: C.nequiv,
                                                border: `1px solid ${C.nequiv}44`,
                                                borderRadius: 4, padding: "1px 7px",
                                                fontSize: 10, fontWeight: 700,
                                                fontFamily: F.mono,
                                            }}>
                                                ≠ not equivalent
                                            </span>
                                        </div>

                                        {/* Human-readable explanation of what the setting means */}
                                        <p style={{
                                            margin: 0,
                                            fontFamily: F.body, fontSize: 12,
                                            color: C.muted, lineHeight: 1.6,
                                        }}>
                                            {SETTING_EXPLANATIONS[s.key]}
                                        </p>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* ── All settings summary grid ── */}
                    <div>
                        <h4 style={{
                            margin: "0 0 10px",
                            fontFamily: F.display, fontSize: 14,
                            fontWeight: 700, color: C.text,
                        }}>
                            All Settings
                        </h4>
                        <div style={{
                            display: "grid",
                            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                            gap: 8,
                        }}>
                            {visibleSettings.map((s) => {
                                const val = row.labels[s.key];
                                const accent =
                                    val === true ? C.equiv :
                                        val === false ? C.nequiv :
                                            C.muted;
                                return (
                                    <div key={s.key} style={{
                                        display: "flex", alignItems: "center",
                                        justifyContent: "space-between",
                                        background: C.faint,
                                        border: `1px solid ${C.border}`,
                                        borderLeft: `4px solid ${s.color}`,
                                        borderRadius: 8,
                                        padding: "10px 14px",
                                    }}>
                                        <span style={{
                                            fontFamily: F.body, fontSize: 12,
                                            fontWeight: 600, color: C.text,
                                        }}>
                                            {s.label}
                                        </span>
                                        <span style={{
                                            fontFamily: F.mono, fontSize: 13,
                                            fontWeight: 700, color: accent,
                                        }}>
                                            {val === true ? "✓" : val === false ? "✗" : "—"}
                                        </span>
                                    </div>
                                );
                            })}
                        </div>
                    </div>

                    {/* ── Equiv / N/A pills ── */}
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        {[
                            { items: equivSettings, col: C.equiv, icon: "✓", label: "equivalent" },
                            { items: counterExamples, col: C.nequiv, icon: "✗", label: "not equivalent" },
                            { items: naSettings, col: C.muted, icon: "—", label: "not evaluated" },
                        ].filter((x) => x.items.length > 0).map(({ items, col, icon, label }) => (
                            <div key={label} style={{
                                background: col + "12",
                                border: `1px solid ${col}30`,
                                borderRadius: 8, padding: "6px 14px",
                                display: "flex", gap: 8, alignItems: "center",
                            }}>
                                <span style={{ fontFamily: F.mono, fontSize: 16, fontWeight: 800, color: col }}>
                                    {items.length}
                                </span>
                                <span style={{ fontFamily: F.body, fontSize: 12, color: col, fontWeight: 600 }}>
                                    setting{items.length !== 1 ? "s" : ""} {label}
                                </span>
                            </div>
                        ))}
                    </div>

                </div>
            </div>
        </div>
    );
}

// ── Setting explanations shown in counter-example cards ────────
const SETTING_EXPLANATIONS: Record<SettingKey, string> = {
    no_constraints:
        "Under no database constraints (no primary keys, foreign keys, or uniqueness), the two queries produce different results on some valid database instance.",
    no_null:
        "When all columns are guaranteed non-null, the predicted query still returns different rows or structure compared to the gold query.",
    positive_only:
        "Restricting to databases with only positive (non-negative) numeric values, the queries diverge — typically exposing sign-dependent logic errors.",
    full_constraints:
        "With all integrity constraints enforced (PKs, FKs, NOT NULL, UNIQUE), the predicted query does not match the gold query's output.",
    set_semantics:
        "Under set semantics (duplicates removed, as in standard SQL DISTINCT behaviour), the queries return different sets of tuples.",
    bag_semantics:
        "Under bag semantics (duplicates preserved, multiset behaviour), the predicted query produces a different multiset of rows than the gold query.",
};

/* ══════════════════════════════════════════════════════════════
   MAIN TABLE COMPONENT
══════════════════════════════════════════════════════════════ */
export default function PairConsistencyTable({
    results,
    activeSettings,
}: Props) {
    const [activeDataset, setActiveDataset] = useState<string>("all");
    const [activeVerdict, setActiveVerdict] = useState<Verdict | "all">("all");
    const [search, setSearch] = useState("");
    const [sortKey, setSortKey] = useState<SortKey>("verdict");
    const [sortDir, setSortDir] = useState<SortDir>("asc");
    const [page, setPage] = useState(1);
    const [selectedRow, setSelectedRow] = useState<AnnotatedRow | null>(null);

    const visibleSettings = useMemo(
        () => activeSettings
            ? SETTINGS.filter((s) => activeSettings.has(s.key))
            : SETTINGS,
        [activeSettings]
    );
    const activeSKeys = visibleSettings.map((s) => s.key);

    const datasets = useMemo(
        () => [...new Set(results.map((r) => r.dataset))].sort(),
        [results]
    );

    const annotated = useMemo<AnnotatedRow[]>(
        () => results.map((r) => ({ ...r, verdict: verdictFor(r, activeSKeys) })),
        [results, activeSKeys]
    );

    const datasetCounts = useMemo(() => {
        const out: Record<string, number> = { all: results.length };
        for (const ds of datasets)
            out[ds] = results.filter((r) => r.dataset === ds).length;
        return out;
    }, [results, datasets]);

    const verdictCounts = useMemo(() => {
        const base = annotated.filter(
            (r) => activeDataset === "all" || r.dataset === activeDataset
        );
        const out: Record<string, number> = { all: base.length };
        for (const v of ["equiv", "diff", "mixed", "empty"] as Verdict[])
            out[v] = base.filter((r) => r.verdict === v).length;
        return out;
    }, [annotated, activeDataset]);

    const processed = useMemo(() => {
        const sq = search.toLowerCase().trim();
        let rows = annotated.filter((r) => {
            if (activeDataset !== "all" && r.dataset !== activeDataset) return false;
            if (activeVerdict !== "all" && r.verdict !== activeVerdict) return false;
            if (sq) {
                const hay = [r.question_id, r.db_id, r.gold ?? "", r.pred ?? ""]
                    .join(" ").toLowerCase();
                if (!hay.includes(sq)) return false;
            }
            return true;
        });

        rows.sort((a, b) => {
            let av: string | number;
            let bv: string | number;
            if (sortKey === "verdict") { av = VERDICT_SORT_ORDER[a.verdict]; bv = VERDICT_SORT_ORDER[b.verdict]; }
            else if (sortKey === "question_id") { av = Number(a.question_id) || a.question_id; bv = Number(b.question_id) || b.question_id; }
            else if (sortKey === "db_id") { av = a.db_id; bv = b.db_id; }
            else {
                const rank = (v: boolean | null | undefined) => v === true ? 2 : v === false ? 1 : 0;
                av = rank(a.labels[sortKey as SettingKey]);
                bv = rank(b.labels[sortKey as SettingKey]);
            }
            if (av < bv) return sortDir === "asc" ? -1 : 1;
            if (av > bv) return sortDir === "asc" ? 1 : -1;
            return 0;
        });

        return rows;
    }, [annotated, activeDataset, activeVerdict, search, sortKey, sortDir]);

    const pages = Math.max(1, Math.ceil(processed.length / PER_PAGE));
    const safePg = Math.min(page, pages);
    const paged = processed.slice((safePg - 1) * PER_PAGE, safePg * PER_PAGE);

    const toggleSort = (k: SortKey) => {
        if (sortKey === k) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
        else { setSortKey(k); setSortDir("asc"); }
        setPage(1);
    };

    const ColHeader = ({ label, sk, align = "left" }: {
        label: string; sk: SortKey; align?: "left" | "center";
    }) => (
        <th onClick={() => toggleSort(sk)} style={{
            padding: "10px 14px", textAlign: align, cursor: "pointer",
            fontFamily: F.body, fontSize: 10, fontWeight: 700,
            color: sortKey === sk ? C.blue : C.muted,
            letterSpacing: "0.07em", textTransform: "uppercase",
            borderBottom: `1px solid ${C.border}`,
            whiteSpace: "nowrap", userSelect: "none",
        }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                {label}
                <SortIcon active={sortKey === sk} dir={sortDir} />
            </span>
        </th>
    );

    if (!results?.length) {
        return (
            <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, padding: "20px 22px", color: C.muted, fontSize: 13, fontFamily: F.body }}>
                No pairs to display.
            </div>
        );
    }

    return (
        <>
            {/* ── Modal ── */}
            {selectedRow && (
                <PairDetailModal
                    row={selectedRow}
                    visibleSettings={visibleSettings as unknown as typeof SETTINGS[number][]}
                    onClose={() => setSelectedRow(null)}
                />
            )}

            <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, overflow: "hidden" }}>

                {/* ── Filters header ── */}
                <div style={{ padding: "20px 22px 0" }}>
                    <h3 style={{ margin: "0 0 4px", fontFamily: F.display, fontSize: 16, color: C.text, fontWeight: 700 }}>
                        Per-Pair Setting Consistency
                    </h3>
                    <p style={{ margin: "0 0 16px", fontSize: 12, color: C.muted, fontFamily: F.body }}>
                        One row per query pair. Click any row to inspect counter-examples.
                    </p>

                    {/* Dataset filter */}
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10, paddingBottom: 10, borderBottom: `1px solid ${C.border}` }}>
                        <span style={{ fontSize: 10, fontWeight: 700, color: C.muted, letterSpacing: "0.07em", textTransform: "uppercase", alignSelf: "center", fontFamily: F.body }}>
                            Dataset
                        </span>
                        <FilterChip label="All" active={activeDataset === "all"} color={C.blue} count={datasetCounts.all} onClick={() => { setActiveDataset("all"); setPage(1); }} />
                        {datasets.map((ds) => (
                            <FilterChip key={ds} label={ds} active={activeDataset === ds} color={C.blue} count={datasetCounts[ds] ?? 0} onClick={() => { setActiveDataset(ds); setPage(1); }} />
                        ))}
                    </div>

                    {/* Verdict filter + search */}
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 14 }}>
                        <span style={{ fontSize: 10, fontWeight: 700, color: C.muted, letterSpacing: "0.07em", textTransform: "uppercase", fontFamily: F.body }}>
                            Verdict
                        </span>
                        <FilterChip label="All" active={activeVerdict === "all"} color={C.muted} count={verdictCounts.all} onClick={() => { setActiveVerdict("all"); setPage(1); }} />
                        {(["mixed", "diff", "equiv"] as Verdict[]).map((v) => (
                            <FilterChip key={v} label={VERDICT_META[v].label} active={activeVerdict === v} color={VERDICT_META[v].color} count={verdictCounts[v] ?? 0} onClick={() => { setActiveVerdict(v); setPage(1); }} />
                        ))}

                        {/* Search */}
                        <div style={{ position: "relative", marginLeft: "auto" }}>
                            <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: C.muted, fontSize: 13, pointerEvents: "none" }}>⌕</span>
                            <input
                                value={search}
                                onChange={(e) => { setSearch(e.target.value); setPage(1); }}
                                placeholder="Search question, db, SQL…"
                                style={{ background: C.panel, border: `1.5px solid ${C.border}`, borderRadius: 8, padding: "6px 32px 6px 30px", fontFamily: F.mono, fontSize: 12, color: C.text, outline: "none", width: 220 }}
                                onFocus={(e) => (e.target.style.borderColor = C.blue)}
                                onBlur={(e) => (e.target.style.borderColor = C.border)}
                            />
                            {search && (
                                <button onClick={() => { setSearch(""); setPage(1); }} style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: C.muted, fontSize: 13, lineHeight: 1 }}>✕</button>
                            )}
                        </div>
                    </div>
                </div>

                {/* ── Table ── */}
                <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse" }}>
                        <thead style={{ background: C.faint }}>
                            <tr>
                                <ColHeader label="Dataset" sk="question_id" />
                                <ColHeader label="Q #" sk="question_id" />
                                <ColHeader label="DB" sk="db_id" />
                                <th style={{ padding: "10px 14px", textAlign: "left", fontFamily: F.body, fontSize: 10, fontWeight: 700, color: C.muted, letterSpacing: "0.07em", textTransform: "uppercase", borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" }}>
                                    Gold SQL
                                </th>
                                {visibleSettings.map((s) => (
                                    <th key={s.key} onClick={() => toggleSort(s.key as SortKey)} style={{ padding: "10px 12px", textAlign: "center", cursor: "pointer", fontFamily: F.body, fontSize: 10, fontWeight: 700, color: sortKey === s.key ? s.color : C.muted, letterSpacing: "0.05em", borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap", userSelect: "none" }}>
                                        <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
                                            <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: s.color, flexShrink: 0 }} />
                                            {s.short}
                                            <SortIcon active={sortKey === s.key} dir={sortDir} />
                                        </span>
                                    </th>
                                ))}
                                <ColHeader label="Verdict" sk="verdict" align="center" />
                                {/* Counter-examples count column */}
                                <th style={{ padding: "10px 12px", textAlign: "center", fontFamily: F.body, fontSize: 10, fontWeight: 700, color: C.muted, letterSpacing: "0.07em", textTransform: "uppercase", borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" }}>
                                    Counter-ex.
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            {paged.length === 0 ? (
                                <tr>
                                    <td colSpan={6 + visibleSettings.length} style={{ padding: "32px", textAlign: "center", color: C.muted, fontFamily: F.body, fontSize: 13 }}>
                                        No pairs match the current filters{search ? ` for "${search}"` : ""}.
                                    </td>
                                </tr>
                            ) : (
                                paged.map((row) => {
                                    const bc = VERDICT_META[row.verdict].color;
                                    const counterCount = visibleSettings.filter(
                                        (s) => row.labels[s.key] === false
                                    ).length;

                                    return (
                                        <tr
                                            key={`${row.dataset}-${row.db_id}-${row.question_id}`}
                                            onClick={() => setSelectedRow(row)}
                                            style={{
                                                borderBottom: `1px solid ${C.border}`,
                                                borderLeft: `3px solid ${bc}`,
                                                cursor: "pointer",
                                                transition: "background 0.1s",
                                            }}
                                            onMouseEnter={(e) => { e.currentTarget.style.background = C.faint; }}
                                            onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                                        >
                                            {/* Dataset */}
                                            <td style={{ padding: "9px 14px" }}>
                                                <span style={{ background: "#dbeafe", color: C.blue, border: "1px solid #bfdbfe", borderRadius: 4, padding: "2px 7px", fontSize: 10, fontWeight: 700, fontFamily: F.mono }}>
                                                    {row.dataset}
                                                </span>
                                            </td>
                                            {/* Q# */}
                                            <td style={{ padding: "9px 14px", fontFamily: F.mono, fontSize: 12, color: C.muted }}>
                                                Q{row.question_id}
                                            </td>
                                            {/* DB */}
                                            <td style={{ padding: "9px 14px", fontFamily: F.mono, fontSize: 11, color: C.muted, whiteSpace: "nowrap" }}>
                                                {row.db_id}
                                            </td>
                                            {/* Gold SQL */}
                                            <td style={{ padding: "9px 14px", fontFamily: F.mono, fontSize: 11, color: C.muted, maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={row.gold ?? row.pred}>
                                                {(row.gold ?? row.pred ?? "").substring(0, 55)}
                                            </td>
                                            {/* Per-setting cells */}
                                            {visibleSettings.map((s) => (
                                                <td key={s.key} style={{ padding: "9px 12px", textAlign: "center" }}>
                                                    <SettingCell value={row.labels[s.key]} />
                                                </td>
                                            ))}
                                            {/* Verdict */}
                                            <td style={{ padding: "9px 14px", textAlign: "center" }}>
                                                <VerdictBadge verdict={row.verdict} />
                                            </td>
                                            {/* Counter-example count */}
                                            <td style={{ padding: "9px 12px", textAlign: "center" }}>
                                                {counterCount > 0 ? (
                                                    <span style={{
                                                        background: C.nequiv + "18",
                                                        color: C.nequiv,
                                                        border: `1px solid ${C.nequiv}44`,
                                                        borderRadius: 10,
                                                        padding: "2px 9px",
                                                        fontSize: 11, fontWeight: 700,
                                                        fontFamily: F.mono,
                                                    }}>
                                                        {counterCount}
                                                    </span>
                                                ) : (
                                                    <span style={{ color: C.muted, fontSize: 12 }}>—</span>
                                                )}
                                            </td>
                                        </tr>
                                    );
                                })
                            )}
                        </tbody>
                    </table>
                </div>

                {processed.length > PER_PAGE && (
                    <Pagination
                        page={safePg} pages={pages}
                        total={processed.length} perPage={PER_PAGE}
                        onPage={(p) => setPage(p)}
                    />
                )}
            </div>
        </>
    );
}