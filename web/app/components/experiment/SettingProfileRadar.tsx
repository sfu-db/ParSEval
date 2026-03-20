"use client";

import { useMemo } from "react";
import {
    ResponsiveContainer,
    RadarChart,
    Radar,
    PolarGrid,
    PolarAngleAxis,
    PolarRadiusAxis,
    Tooltip,
    Legend,
} from "recharts";

import { EvalResult } from "@/lib/types";

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

interface Props {
    results: EvalResult[];
}

// One row per setting, one column per dataset.
// { subject: "No Constr.", settingLabel: "No Constraints", spider: 72, tpch: 58 }
type RadarRow = {
    subject: string;   // short  → angle-axis label
    settingLabel: string;   // label  → tooltip header
    [dataset: string]: string | number;
};

function equivRateForKey(
    rows: EvalResult[],
    key: SettingKey
): number {
    const evaluated = rows.filter(
        (r) => r.labels[key] !== null && r.labels[key] !== undefined
    );
    if (!evaluated.length) return 0;
    const equiv = evaluated.filter((r) => r.labels[key] === true).length;
    return Math.round((equiv / evaluated.length) * 100);
}

export default function SettingProfileRadar({ results }: Props) {
    const datasets = useMemo(
        () => [...new Set(results.map((r) => r.dataset))].sort(),
        [results]
    );

    const datasetColor = useMemo(
        () =>
            Object.fromEntries(
                datasets.map((ds, i) => [
                    ds,
                    DATASET_COLORS[i % DATASET_COLORS.length],
                ])
            ),
        [datasets]
    );

    // One row per setting; each dataset gets its own numeric column.
    const data: RadarRow[] = useMemo(() => {
        if (!results?.length) return [];

        return SETTINGS.map((s) => {
            const row: RadarRow = {
                subject: s.short,
                settingLabel: s.label,
            };
            for (const ds of datasets) {
                const dsRows = results.filter((r) => r.dataset === ds);
                const rate = equivRateForKey(dsRows, s.key);
                row[ds] = rate === null ? 0 : rate;
            }
            return row;
        });
    }, [results, datasets]);

    if (!data.length) {
        return (
            <div className="flex items-center justify-center h-40 text-sm text-gray-400">
                No data available.
            </div>
        );
    }

    return (
        <ResponsiveContainer width="100%" height={320}>
            <RadarChart
                data={data}
                cx="50%"
                cy="50%"
                outerRadius="65%"
                margin={{ top: 16, right: 32, bottom: 16, left: 32 }}
            >
                <PolarGrid stroke="#e2e8f0" />

                {/* ── Angle axis: setting shorts ── */}
                <PolarAngleAxis
                    dataKey="subject"
                    tick={({ x, y, payload }) => {
                        // Cast x/y to number for arithmetic
                        const nx = typeof x === "number" ? x : Number(x);
                        const ny = typeof y === "number" ? y : Number(y);
                        // Split label into lines
                        const words = String(payload.value).split(" ");
                        return (
                            <g>
                                {words.map((word, i) => (
                                    <text
                                        key={i}
                                        x={nx}
                                        y={ny + i * 13}
                                        textAnchor="middle"
                                        dominantBaseline={i === 0 ? "auto" : "hanging"}
                                        fontSize={11}
                                        fontWeight={600}
                                        fill="#6e7781"
                                    >
                                        {word}
                                    </text>
                                ))}
                            </g>
                        );
                    }}
                />

                {/* ── Radius axis: 0–100 with % labels ── */}
                <PolarRadiusAxis
                    domain={[0, 100]}
                    tickCount={5}
                    tickFormatter={(v) => `${v}%`}
                    tick={{ fontSize: 9, fill: "#94a3b8" }}
                    axisLine={false}
                    angle={30}      // angle to avoid label collisions
                />

                <Tooltip
                    formatter={(value, name) => [
                        `${String(value)}%`,
                        String(name),
                    ]}
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

                <Legend
                    iconSize={10}
                    iconType="circle"
                    wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
                />

                {/* One <Radar> per dataset */}
                {datasets.map((ds) => (
                    <Radar
                        key={ds}
                        name={ds}
                        dataKey={ds}
                        stroke={datasetColor[ds]}
                        fill={datasetColor[ds]}
                        fillOpacity={0.12}
                        strokeWidth={2}
                        dot={{
                            r: 3,
                            fill: datasetColor[ds],
                            strokeWidth: 0,
                        }}
                        activeDot={{ r: 5 }}
                    />
                ))}
            </RadarChart>
        </ResponsiveContainer>
    );
}