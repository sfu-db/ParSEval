"use client";

import { useMemo } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    Legend,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import { ChartWrapper } from "./ChartWrapper";
import { getSettingsForRows, MODEL_LINE_COLORS, type SettingMeta } from "@/lib/settings";
import type { EvalRecord } from "@/lib/types";

type ChartRowLike = Pick<EvalRecord, "dataset" | "labels">;

type DrilldownInfo = {
    dataset: string;
    settingKey: string;
    settingLabel: string;
    value: number;
};

interface Props {
    results: ChartRowLike[];
    activeDataset?: string | null;
    onLegendClick?: (dataset: string | null) => void;
    onDrillDown?: (info: DrilldownInfo) => void;
}

function formatPercent(value: number | null | undefined) {
    if (typeof value !== "number" || Number.isNaN(value)) return "--";
    return `${value.toFixed(1)}%`;
}

function TickLabel({ x = 0, y = 0, payload }: { x?: number; y?: number; payload?: { value?: string } }) {
    const text = payload?.value ?? "";
    const lines = text.includes(" / ") ? text.split(" / ") : text.split(" ");

    return (
        <g transform={`translate(${x},${y})`}>
            {lines.map((line, index) => (
                <text
                    key={`${line}-${index}`}
                    x={0}
                    y={index * 12}
                    dy={16}
                    textAnchor="middle"
                    fill="#334155"
                    fontSize={11}
                    fontWeight={600}
                >
                    {line}
                </text>
            ))}
        </g>
    );
}

export default function SettingRateBarChart({ results, activeDataset = null, onLegendClick, onDrillDown }: Props) {
    const settings = useMemo(() => getSettingsForRows(results), [results]);

    const datasetOrder = useMemo(() => {
        return Array.from(new Set(results.map((row) => row.dataset).filter(Boolean)));
    }, [results]);

    const colorMap = useMemo(() => {
        return Object.fromEntries(
            datasetOrder.map((dataset, index) => [dataset, MODEL_LINE_COLORS[index % MODEL_LINE_COLORS.length]])
        ) as Record<string, string>;
    }, [datasetOrder]);

    const chartData = useMemo(() => {
        return settings.map((setting: SettingMeta) => {
            const row: Record<string, string | number | null> = {
                key: setting.key,
                label: setting.short,
                fullLabel: setting.label,
                color: setting.color,
            };

            for (const dataset of datasetOrder) {
                const matching = results.filter((result) => result.dataset === dataset);
                const evaluated = matching.filter((result) => result.labels?.[setting.key] !== null && result.labels?.[setting.key] !== undefined);
                const equivalent = evaluated.filter((result) => result.labels?.[setting.key] === true);
                row[dataset] = evaluated.length ? (equivalent.length / evaluated.length) * 100 : null;
            }

            return row;
        });
    }, [datasetOrder, results, settings]);

    const visibleDatasets = useMemo(() => {
        if (activeDataset) return datasetOrder.filter((dataset) => dataset === activeDataset);
        return datasetOrder;
    }, [activeDataset, datasetOrder]);

    if (!results.length || !settings.length || !datasetOrder.length) {
        return <div className="flex h-full items-center justify-center text-sm text-muted-foreground">No execution accuracy data available.</div>;
    }

    return (
        <ChartWrapper>
            <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 8, right: 20, bottom: 40, left: 0 }} barGap={10}>
                    <CartesianGrid strokeDasharray="4 4" vertical={false} stroke="#cbd5e1" />
                    <XAxis dataKey="label" tick={<TickLabel />} interval={0} height={66} />
                    <YAxis domain={[0, 100]} tick={{ fill: "#334155", fontSize: 12 }} />
                    <Tooltip
                        formatter={(value: number | null, name: string, item) => {
                            const payload = item.payload as { fullLabel?: string } | undefined;
                            return [formatPercent(value), payload?.fullLabel ?? name];
                        }}
                        labelFormatter={(label: string) => label}
                        contentStyle={{
                            borderRadius: 10,
                            border: "1px solid #cbd5e1",
                            background: "#ffffff",
                            color: "#0f172a",
                            boxShadow: "0 10px 30px rgba(15, 23, 42, 0.12)",
                        }}
                    />
                    <Legend
                        wrapperStyle={{ paddingTop: 8, cursor: onLegendClick ? "pointer" : "default", color: "#334155" }}
                        onClick={(entry: { value?: string } | undefined) => {
                            const dataset = entry?.value;
                            if (!dataset || !onLegendClick) return;
                            onLegendClick(activeDataset === dataset ? null : dataset);
                        }}
                    />
                    {visibleDatasets.map((dataset) => (
                        <Bar
                            key={dataset}
                            dataKey={dataset}
                            name={dataset}
                            fill={colorMap[dataset] ?? "#1d4ed8"}
                            radius={[6, 6, 0, 0]}
                            maxBarSize={40}
                            onClick={(data) => {
                                if (!onDrillDown || !data) return;
                                const payload = data as Record<string, string | number | null>;
                                const value = payload[dataset];
                                onDrillDown({
                                    dataset,
                                    settingKey: String(payload.key ?? ""),
                                    settingLabel: String(payload.fullLabel ?? payload.label ?? ""),
                                    value: typeof value === "number" ? value : 0,
                                });
                            }}
                        >
                            {chartData.map((entry) => (
                                <Cell key={`${dataset}-${entry.key}`} fill={colorMap[dataset] ?? "#1d4ed8"} opacity={activeDataset && activeDataset !== dataset ? 0.28 : 0.96} />
                            ))}
                        </Bar>
                    ))}
                </BarChart>
            </ResponsiveContainer>
        </ChartWrapper>
    );
}
