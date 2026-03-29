"use client";

import { useMemo } from "react";
import { ResponsiveContainer, Sankey, Tooltip } from "recharts";
import { getSettingsForRows, type SettingKey } from "@/lib/settings";
import { EvalRecord } from "@/lib/types";
import { ChartWrapper } from "./ChartWrapper";

type SettingState = "equiv" | "diff";

type SankeyNode = {
    name: string;
    setting: string;
    state: SettingState;
    fill: string;
};

type SankeyLink = {
    source: number;
    target: number;
    value: number;
};

const STATE_META: Record<SettingState, { label: string; fill: string }> = {
    equiv: { label: "Equiv", fill: "#0b6e4f" },
    diff: { label: "Diff", fill: "#b02e0c" },
};

function getRowState(result: EvalRecord, key: SettingKey): SettingState | null {
    const value = result.labels[key];
    if (value === true) return "equiv";
    if (value === false) return "diff";
    return null;
}

function nodeIndex(settingIndex: number, state: SettingState) {
    return settingIndex * 2 + (state === "equiv" ? 0 : 1);
}

type CustomNodeProps = {
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    payload: SankeyNode;
};

function CustomNode(props: CustomNodeProps) {
    const { x, y, width, height, payload } = props;
    return (
        <g>
            <rect x={x} y={y} width={width} height={height} fill={payload.fill} fillOpacity={0.9} rx={4} />
            <text
                x={x + width / 2}
                y={y - 8}
                textAnchor="middle"
                fontSize={11}
                fontWeight={700}
                fill="#1e293b"
            >
                {payload.setting}
            </text>
            <text
                x={x + width / 2}
                y={y + height / 2}
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize={11}
                fontWeight={700}
                fill="#ffffff"
            >
                {payload.state === "equiv" ? "Eq" : "Df"}
            </text>
        </g>
    );
}

export function SettingStateSankey({ results }: { results: EvalRecord[] }) {
    const settings = useMemo(() => getSettingsForRows(results), [results]);

    const data = useMemo(() => {
        const nodes: SankeyNode[] = settings.flatMap((setting) => [
            {
                name: `${setting.short}: ${STATE_META.equiv.label}`,
                setting: setting.short,
                state: "equiv",
                fill: STATE_META.equiv.fill,
            },
            {
                name: `${setting.short}: ${STATE_META.diff.label}`,
                setting: setting.short,
                state: "diff",
                fill: STATE_META.diff.fill,
            },
        ]);

        const linkMap = new Map<string, number>();

        results.forEach((result) => {
            for (let i = 0; i < settings.length - 1; i += 1) {
                const current = getRowState(result, settings[i].key);
                const next = getRowState(result, settings[i + 1].key);
                if (!current || !next) continue;

                const source = nodeIndex(i, current);
                const target = nodeIndex(i + 1, next);
                const key = `${source}-${target}`;
                linkMap.set(key, (linkMap.get(key) ?? 0) + 1);
            }
        });

        const links: SankeyLink[] = Array.from(linkMap.entries()).map(([key, value]) => {
            const [source, target] = key.split("-").map(Number);
            return { source, target, value };
        });

        return { nodes, links };
    }, [results, settings]);

    if (!results.length || data.links.length === 0) {
        return <div className="flex h-full items-center justify-center text-sm text-muted-foreground">No state flow available.</div>;
    }

    return (
        <div className="space-y-3">
            <div>
                <div className="text-lg font-semibold text-foreground">Setting State Flow</div>
                <div className="mt-1 text-sm text-muted-foreground">
                    Flow of pair states across the ordered settings, from equivalent to different outcomes.
                </div>
            </div>
            <div className="h-[420px] rounded-xl border border-border bg-background/80 p-3 shadow-[0_1px_0_rgba(15,23,42,0.03)]">
                <ChartWrapper>
                    <ResponsiveContainer width="100%" height="100%">
                        <Sankey
                            data={data}
                            node={<CustomNode />}
                            nodePadding={28}
                            nodeWidth={18}
                            margin={{ top: 24, right: 24, bottom: 12, left: 24 }}
                            link={{ stroke: "#64748b", strokeOpacity: 0.3 }}
                        >
                            <Tooltip
                                formatter={(value: number) => [`${value} pairs`, "Flow"]}
                                contentStyle={{
                                    borderRadius: 8,
                                    border: "1px solid #cbd5e1",
                                    background: "#ffffff",
                                    color: "#0f172a",
                                    fontSize: 12,
                                    boxShadow: "0 10px 30px rgba(15,23,42,0.12)",
                                }}
                            />
                        </Sankey>
                    </ResponsiveContainer>
                </ChartWrapper>
            </div>
        </div>
    );
}
