"use client";

import { useMemo, useState } from "react";
import { getSettingExplanation, getSettingsForRows, type SettingKey } from "@/lib/settings";
import { EvalRecord } from "@/lib/types";

export type HeatmapViewMode = "joint_equiv" | "disagree";

export type HeatmapSelection = {
    rowKey: SettingKey;
    colKey: SettingKey;
    mode: HeatmapViewMode;
};

type MatrixCell = {
    rowKey: SettingKey;
    colKey: SettingKey;
    value: number | null;
    count: number;
};

const C = {
    border: "#dde1e7",
    text: "#1c2128",
    success: "26,127,55",
    danger: "207,34,46",
    activeRing: "#0f172a",
};

const F = {
    mono: "'JetBrains Mono', monospace",
    body: "'DM Sans', sans-serif",
};

function valueLabel(value: number | null) {
    if (value === null) return "--";
    return `${Math.round(value * 100)}%`;
}

function jointRate(rows: EvalRecord[], left: SettingKey, right: SettingKey, mode: HeatmapViewMode) {
    const evaluated = rows.filter((row) => row.labels[left] !== undefined && row.labels[right] !== undefined);
    if (!evaluated.length) return { value: null, count: 0 };

    if (mode === "joint_equiv") {
        const bothEquivalent = evaluated.filter((row) => row.labels[left] === true && row.labels[right] === true).length;
        return { value: bothEquivalent / evaluated.length, count: evaluated.length };
    }

    const disagreement = evaluated.filter((row) => row.labels[left] !== row.labels[right]).length;
    return { value: disagreement / evaluated.length, count: evaluated.length };
}

interface Props {
    results: EvalRecord[];
    selectedCell: HeatmapSelection | null;
    onCellSelect: (selection: HeatmapSelection | null) => void;
}

export function SettingConfusionHeatmap({ results, selectedCell, onCellSelect }: Props) {
    const [viewMode, setViewMode] = useState<HeatmapViewMode>("joint_equiv");

    const settings = useMemo(() => getSettingsForRows(results), [results]);

    const matrix = useMemo(() => {
        return settings.map((row) =>
            settings.map((col) => {
                const metrics = jointRate(results, row.key, col.key, viewMode);
                return {
                    rowKey: row.key,
                    colKey: col.key,
                    value: metrics.value,
                    count: metrics.count,
                } satisfies MatrixCell;
            })
        );
    }, [results, settings, viewMode]);

    if (!results.length) {
        return <div className="flex h-full items-center justify-center text-sm text-slate-400">No data available.</div>;
    }

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="text-lg font-semibold text-slate-900">Multi-Setting Confusion</div>
                    <div className="mt-1 text-sm text-slate-500">
                        {viewMode === "joint_equiv"
                            ? "Each cell shows how many pairs are equivalent under both settings at the same time. Click a cell to filter the table below."
                            : "Each cell shows how many pairs are not agreed upon by the two settings. Click a cell to filter the table below."}
                    </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    {selectedCell ? (
                        <button
                            type="button"
                            className="rounded-md border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600"
                            onClick={() => onCellSelect(null)}
                        >
                            Clear Cell Filter
                        </button>
                    ) : null}
                    <div className="inline-flex rounded-lg border border-slate-200 bg-white p-1">
                        <button
                            type="button"
                            className={`rounded-md px-3 py-1.5 text-sm font-medium ${viewMode === "joint_equiv" ? "bg-slate-900 text-white" : "text-slate-600"}`}
                            onClick={() => {
                                setViewMode("joint_equiv");
                                if (selectedCell?.mode !== "joint_equiv") onCellSelect(null);
                            }}
                        >
                            Both Equiv
                        </button>
                        <button
                            type="button"
                            className={`rounded-md px-3 py-1.5 text-sm font-medium ${viewMode === "disagree" ? "bg-slate-900 text-white" : "text-slate-600"}`}
                            onClick={() => {
                                setViewMode("disagree");
                                if (selectedCell?.mode !== "disagree") onCellSelect(null);
                            }}
                        >
                            Not Agree
                        </button>
                    </div>
                </div>
            </div>

            <div className="overflow-x-auto">
                <table className="w-full border-collapse" style={{ fontFamily: F.mono, fontSize: 12 }}>
                    <thead>
                        <tr>
                            <th
                                className="sticky left-0 z-10 bg-slate-50 px-3 py-2 text-left text-[11px] font-bold uppercase tracking-[0.06em] text-slate-500"
                                style={{ borderBottom: `1px solid ${C.border}`, fontFamily: F.body }}
                            >
                                Setting
                            </th>
                            {settings.map((setting) => (
                                <th
                                    key={setting.key}
                                    className="px-3 py-2 text-center text-[11px] font-bold"
                                    style={{ borderBottom: `1px solid ${C.border}`, color: setting.color, fontFamily: F.body }}
                                >
                                    {setting.short}<span className="ml-1 inline-flex h-4 w-4 items-center justify-center rounded-full border border-slate-300 text-[10px] font-bold text-slate-500" title={getSettingExplanation(setting.key)}>?</span>
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {settings.map((row, rowIndex) => (
                            <tr key={row.key}>
                                <td
                                    className="sticky left-0 bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-800"
                                    style={{ borderBottom: `1px solid ${C.border}`, fontFamily: F.body }}
                                >
                                    {row.short}<span className="ml-1 inline-flex h-4 w-4 items-center justify-center rounded-full border border-slate-300 text-[10px] font-bold text-slate-500" title={getSettingExplanation(row.key)}>?</span>
                                </td>
                                {matrix[rowIndex].map((cell) => {
                                    const alpha = cell.value === null ? 0 : Math.max(0.08, cell.value * 0.82);
                                    const background = cell.value === null
                                        ? "transparent"
                                        : `rgba(${viewMode === "joint_equiv" ? C.success : C.danger}, ${alpha.toFixed(2)})`;
                                    const textColor = cell.value !== null && alpha > 0.45 ? "#ffffff" : C.text;
                                    const colLabel = settings.find((item) => item.key === cell.colKey)?.label ?? cell.colKey;
                                    const title = cell.value === null
                                        ? "No overlapping evaluations"
                                        : viewMode === "joint_equiv"
                                            ? `${row.label} + ${colLabel}: ${valueLabel(cell.value)} both equivalent across ${cell.count} rows`
                                            : `${row.label} + ${colLabel}: ${valueLabel(cell.value)} disagreement across ${cell.count} rows`;
                                    const isSelected = selectedCell?.mode === viewMode && selectedCell.rowKey === cell.rowKey && selectedCell.colKey === cell.colKey;
                                    const isDisabled = cell.count === 0;

                                    return (
                                        <td
                                            key={cell.colKey}
                                            className="px-2 py-2 text-center"
                                            style={{ borderBottom: `1px solid ${C.border}` }}
                                            title={title}
                                        >
                                            <button
                                                type="button"
                                                className="mx-auto flex h-11 w-16 items-center justify-center rounded-md border text-xs font-bold transition"
                                                style={{
                                                    background,
                                                    borderColor: isSelected ? C.activeRing : C.border,
                                                    color: textColor,
                                                    boxShadow: isSelected ? `0 0 0 2px ${C.activeRing}22` : "none",
                                                    cursor: isDisabled ? "default" : "pointer",
                                                    opacity: isDisabled ? 0.55 : 1,
                                                }}
                                                onClick={() => {
                                                    if (isDisabled) return;
                                                    onCellSelect(
                                                        isSelected
                                                            ? null
                                                            : { rowKey: cell.rowKey, colKey: cell.colKey, mode: viewMode }
                                                    );
                                                }}
                                                disabled={isDisabled}
                                            >
                                                {valueLabel(cell.value)}
                                            </button>
                                        </td>
                                    );
                                })}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
