"use client";

import { useMemo } from "react";
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
};
const F = {
    display: "'Syne', sans-serif",
    mono: "'JetBrains Mono', monospace",
    body: "'DM Sans', sans-serif",
};

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

// ── Types ─────────────────────────────────────────────────────
interface Props {
    results: EvalResult[];
    activeSettings?: Set<SettingKey>;   // if omitted, all settings shown
    onDrillDown?: (info: { type: "dataset"; key: string; label: string }) => void;
}

type DatasetRow = {
    dataset: string;
    n: number;
} & { [K in SettingKey]?: number | null };

// ── Helpers ───────────────────────────────────────────────────
function equivRate(rows: EvalResult[], key: SettingKey): number | null {
    const evaluated = rows.filter(
        (r) => r.labels[key] !== null && r.labels[key] !== undefined
    );
    if (!evaluated.length) return null;
    return evaluated.filter((r) => r.labels[key] === true).length / evaluated.length;
}

// ── Component ─────────────────────────────────────────────────
export default function EquivalenceHeatmap({
    results,
    activeSettings,
    onDrillDown,
}: Props) {
    // Which settings to show — default to all
    const visibleSettings = useMemo(
        () =>
            activeSettings
                ? SETTINGS.filter((s) => activeSettings.has(s.key))
                : SETTINGS,
        [activeSettings]
    );

    // Derive datasets from results
    const datasets = useMemo(
        () => [...new Set(results.map((r) => r.dataset))].sort(),
        [results]
    );

    // One row per dataset: equiv rate per setting + total n
    const datasetRows: DatasetRow[] = useMemo(() => {
        return datasets.map((ds) => {
            const dsRows = results.filter((r) => r.dataset === ds);
            const row: DatasetRow = { dataset: ds, n: dsRows.length };
            for (const s of SETTINGS) {
                row[s.key] = equivRate(dsRows, s.key);
            }
            return row;
        });
    }, [results, datasets]);

    if (!results?.length) {
        return (
            <div
                style={{
                    color: C.muted,
                    fontSize: 13,
                    fontFamily: F.body,
                }}
            >
                No data available.
            </div>
        );
    }

    return (
        <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", height: "100%" }}>
            {/* Header */}
            <h3
                style={{
                    margin: "0 0 4px",
                    fontFamily: F.display,
                    fontSize: 16,
                    color: C.text,
                    fontWeight: 700,
                }}
            >
                {/* Equivalence Rate Heatmap */}
            </h3>


            <div style={{ overflowX: "auto" }}>
                <table
                    style={{
                        borderCollapse: "collapse",
                        width: "100%",
                        fontFamily: F.mono,
                        fontSize: 12,
                    }}
                >
                    {/* ── Head ── */}
                    <thead>
                        <tr>
                            <th
                                style={{
                                    padding: "8px 16px",
                                    textAlign: "left",
                                    color: C.muted,
                                    fontWeight: 700,
                                    fontSize: 11,
                                    borderBottom: `1px solid ${C.border}`,
                                    letterSpacing: "0.06em",
                                    textTransform: "uppercase",
                                    fontFamily: F.body,
                                }}
                            >
                                Dataset
                            </th>

                            {visibleSettings.map((s) => (
                                <th
                                    key={s.key}
                                    style={{
                                        padding: "8px 16px",
                                        textAlign: "center",
                                        color: s.color,
                                        fontWeight: 700,
                                        fontSize: 11,
                                        borderBottom: `1px solid ${C.border}`,
                                        whiteSpace: "nowrap",
                                        fontFamily: F.body,
                                    }}
                                >
                                    {s.short}
                                </th>
                            ))}

                            <th
                                style={{
                                    padding: "8px 16px",
                                    textAlign: "right",
                                    color: C.muted,
                                    fontWeight: 700,
                                    fontSize: 11,
                                    borderBottom: `1px solid ${C.border}`,
                                    letterSpacing: "0.06em",
                                    textTransform: "uppercase",
                                    fontFamily: F.body,
                                }}
                            >
                                n
                            </th>
                        </tr>
                    </thead>

                    {/* ── Body ── */}
                    <tbody>
                        {datasetRows.map((row) => (
                            <tr
                                key={row.dataset}
                                onClick={() =>
                                    onDrillDown?.({
                                        type: "dataset",
                                        key: row.dataset,
                                        label: row.dataset,
                                    })
                                }
                                style={{
                                    cursor: onDrillDown ? "pointer" : "default",
                                    borderBottom: `1px solid ${C.border}`,
                                    transition: "background 0.1s",
                                }}
                                onMouseEnter={(e) => {
                                    if (onDrillDown)
                                        e.currentTarget.style.background = C.faint;
                                }}
                                onMouseLeave={(e) => {
                                    e.currentTarget.style.background = "transparent";
                                }}
                            >
                                {/* Dataset name */}
                                <td
                                    style={{
                                        padding: "10px 16px",
                                        fontWeight: 700,
                                        color: C.text,
                                        fontFamily: F.body,
                                        fontSize: 13,
                                    }}
                                >
                                    {row.dataset}
                                </td>

                                {/* One cell per setting */}
                                {visibleSettings.map((s) => {
                                    const rate = row[s.key] ?? null;
                                    const alpha =
                                        rate !== null
                                            ? Math.max(0.08, rate * 0.75)
                                            : 0;
                                    const bgColor =
                                        rate !== null
                                            ? `rgba(26,127,55,${alpha.toFixed(2)})`
                                            : "transparent";
                                    // Use white text once background is dark enough
                                    const textColor =
                                        rate !== null && rate > 0.6
                                            ? "#ffffff"
                                            : C.text;

                                    return (
                                        <td
                                            key={s.key}
                                            style={{
                                                padding: "8px 16px",
                                                textAlign: "center",
                                            }}
                                        >
                                            <div
                                                style={{
                                                    display: "inline-flex",
                                                    alignItems: "center",
                                                    justifyContent: "center",
                                                    width: 56,
                                                    height: 28,
                                                    background: bgColor,
                                                    border: `1px solid ${C.border}`,
                                                    borderRadius: 6,
                                                    transition: "background 0.2s",
                                                }}
                                                title={
                                                    rate !== null
                                                        ? `${s.label}: ${pct(rate)}`
                                                        : `${s.label}: not evaluated`
                                                }
                                            >
                                                <span
                                                    style={{
                                                        color: textColor,
                                                        fontSize: 11,
                                                        fontWeight: 700,
                                                        fontFamily: F.mono,
                                                    }}
                                                >
                                                    {rate !== null ? pct(rate) : "—"}
                                                </span>
                                            </div>
                                        </td>
                                    );
                                })}

                                {/* Total count */}
                                <td
                                    style={{
                                        padding: "10px 16px",
                                        textAlign: "right",
                                        color: C.muted,
                                        fontFamily: F.mono,
                                        fontSize: 12,
                                    }}
                                >
                                    {row.n.toLocaleString()}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                <p
                    style={{
                        margin: "0 0 18px",
                        fontSize: 12,
                        color: C.muted,
                        fontFamily: F.body,
                    }}
                >
                    Dataset × Constraint Setting — darker green = more pairs
                    equivalent
                    {onDrillDown && " · click row to drill down"}
                </p>
            </div>
        </div>
    );
}