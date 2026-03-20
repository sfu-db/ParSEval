"use client";

import {
    ResponsiveContainer,
    BarChart,
    Bar,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    Legend,
    LabelList,
} from "recharts";

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

const DATASET_COLORS = [
    "#6366f1", "#f59e0b", "#10b981",
    "#ef4444", "#8b5cf6", "#0ea5e9",
];

// ── Props ─────────────────────────────────────────────────────
interface Props {
    results: EvalResult[];
    activeDataset?: string | null;
    onLegendClick?: (dataset: string | null) => void;
    onDrillDown?: (info: { type: "setting"; key: string; label: string }) => void;
}

// One row per setting; one numeric column per dataset
type ChartRow = {
    setting: string;       // s.short  → X-axis label
    settingKey: SettingKey;
    settingLabel: string;       // s.label  → tooltip header
    [dataset: string]: string | number;
};

// ── Bug fix 1: BarValueLabel must be a plain SVG <text> ───────
// The previous version had an entire BarChart copy-pasted here,
// which caused an infinite render loop and a broken DOM structure.
const MIN_BAR_HEIGHT_FOR_LABEL = 24;

function BarValueLabel(props: {
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    value?: number;
}) {
    const { x = 0, y = 0, width = 0, height = 0, value = 0 } = props;
    if (height < MIN_BAR_HEIGHT_FOR_LABEL || value === 0) return null;
    return (
        <text
            x={x + width / 2}
            y={y + height / 2}
            textAnchor="middle"
            dominantBaseline="middle"
            fontSize={11}
            fontWeight={700}
            fill="#ffffff"
            style={{ pointerEvents: "none" }}
        >
            {value}%
        </text>
    );
}

// ── Two-line X-axis tick ───────────────────────────────────────
// Splits "No Constr." → ["No", "Constr."] and stacks them so
// labels stay readable without rotation clipping.
function SettingTick({
    x,
    y,
    payload,
}: {
    x?: number;
    y?: number;
    payload?: { value: string };
}) {
    const words = (payload?.value ?? "").split(" ");
    return (
        <g transform={`translate(${x},${y})`}>
            {words.map((word, i) => (
                <text
                    key={i}
                    x={0}
                    y={0}
                    dy={14 + i * 14}
                    textAnchor="middle"
                    fontSize={12}
                    fontWeight={600}
                    fill="#6e7781"
                >
                    {word}
                </text>
            ))}
        </g>
    );
}

// ── Helpers ───────────────────────────────────────────────────
function equivRateForKey(rows: EvalResult[], key: SettingKey): number {
    const evaluated = rows.filter(
        (r) => r.labels[key] !== null && r.labels[key] !== undefined
    );
    if (!evaluated.length) return 0;
    const equiv = evaluated.filter((r) => r.labels[key] === true).length;
    return Math.round((equiv / evaluated.length) * 100);
}

// ── Main component ────────────────────────────────────────────
export default function SettingRateBarChart({
    results,
    activeDataset,
    onLegendClick,
    onDrillDown,
}: Props) {

    // Unique sorted dataset names derived from results
    const datasets = useMemo(
        () => [...new Set(results.map((r) => r.dataset))].sort(),
        [results]
    );

    // Stable dataset → color mapping
    const datasetColor = useMemo(
        () => Object.fromEntries(
            datasets.map((ds, i) => [ds, DATASET_COLORS[i % DATASET_COLORS.length]])
        ),
        [datasets]
    );

    // Bug fix 2 & 3: build chartData from results.labels, not results.rates
    // One row per setting, one numeric column per dataset.
    const chartData: ChartRow[] = useMemo(() => {
        if (!results?.length) return [];

        return SETTINGS.map((s) => {
            const row: ChartRow = {
                setting: s.short,
                settingKey: s.key,
                settingLabel: s.label,
            };
            for (const ds of datasets) {
                const dsRows = results.filter((r) => r.dataset === ds);
                row[ds] = equivRateForKey(dsRows, s.key);
            }
            return row;
        });
    }, [results, datasets]);

    if (!results?.length) {
        return (
            <div className="flex items-center justify-center h-40 text-sm text-gray-400">
                No results to display.
            </div>
        );
    }

    // Datasets actually shown (filtered when one is active)
    const visibleDatasets = activeDataset
        ? datasets.filter((ds) => ds === activeDataset)
        : datasets;

    const barSize = 28;
    const groupHeight = datasets.length * (barSize + 6) + 24;
    const chartHeight = Math.max(360, SETTINGS.length * groupHeight + 80);

    return (
        <div className="w-full h-full">
            <ResponsiveContainer width="100%" height="100%">
                <BarChart
                    data={chartData}
                    barCategoryGap="28%"
                    barGap={4}
                    margin={{ top: 8, right: 24, bottom: 48, left: 8 }}
                >
                    <CartesianGrid
                        strokeDasharray="3 3"
                        vertical={false}
                        stroke="#e2e8f0"
                    />

                    <XAxis
                        dataKey="setting"
                        tick={<SettingTick />}
                        axisLine={false}
                        tickLine={false}
                        interval={0}
                        height={48}
                    />

                    <YAxis
                        domain={[0, 100]}
                        tickFormatter={(v) => `${v}%`}
                        tick={{ fontSize: 11, fill: "#6e7781" }}
                        axisLine={false}
                        tickLine={false}
                        width={42}
                    />

                    <Tooltip
                        cursor={{ fill: "#f1f5f9" }}
                        formatter={(value: number, name: string) => [`${value}%`, name]}
                        labelFormatter={(_, payload) =>
                            payload?.[0]?.payload?.settingLabel ?? ""
                        }
                        contentStyle={{
                            borderRadius: 8,
                            border: "1px solid #dde1e7",
                            fontSize: 12,
                            boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
                        }}
                    />

                    {/*
                     * Clickable legend — clicking a dataset name toggles it.
                     * Active dataset is highlighted; all others are dimmed.
                     * Clicking the same dataset again resets to "show all".
                     *
                     * recharts Legend onClick fires with { dataKey, value, ... }
                     * so we read e.dataKey for the dataset name.
                     */}
                    <Legend
                        verticalAlign="top"
                        align="right"
                        iconSize={10}
                        iconType="circle"
                        wrapperStyle={{ fontSize: 12, paddingBottom: 16 }}
                        formatter={(value: string) => (
                            // Dim datasets that are not the active one
                            <span style={{
                                color: !activeDataset || activeDataset === value
                                    ? "#1c2128"
                                    : "#c0c8d0",
                                fontWeight: activeDataset === value ? 700 : 400,
                                cursor: "pointer",
                                transition: "color 0.15s",
                            }}>
                                {value}
                            </span>
                        )}
                        onClick={(e) => {
                            if (!onLegendClick) return;
                            const clicked = e?.dataKey ? String(e.dataKey) : null;
                            // Toggle: clicking the already-active dataset resets to all
                            onLegendClick(clicked === activeDataset ? null : clicked);
                        }}
                    />

                    {/*
                     * Render one <Bar> per VISIBLE dataset.
                     * When a dataset is active, only its bar renders.
                     * The bar fill is always the dataset's stable color —
                     * opacity drops for inactive datasets via the legend formatter.
                     */}
                    {visibleDatasets.map((ds) => (
                        <Bar
                            key={ds}
                            dataKey={ds}
                            name={ds}
                            fill={datasetColor[ds]}
                            radius={[4, 4, 0, 0]}
                            barSize={barSize}
                            cursor={onDrillDown ? "pointer" : "default"}
                            onClick={(barData) => {
                                onDrillDown?.({
                                    type: "setting",
                                    key: barData.settingKey,
                                    label: barData.settingLabel,
                                });
                            }}
                        >
                            <LabelList
                                dataKey={ds}
                                content={<BarValueLabel />}
                            />
                        </Bar>
                    ))}
                </BarChart>
            </ResponsiveContainer>
        </div>
    );
}