"use client";

import { useMemo } from "react";
import { ResponsiveContainer, Sankey, Tooltip } from "recharts";
import { getSettingsForRows, type SettingKey } from "@/lib/settings";
import { EvalRecord } from "@/lib/types";

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
    equiv: { label: "Equiv", fill: "#16a34a" },
    diff: { label: "Diff", fill: "#dc2626" },
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
                fill="#334155"
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
        return <div className="flex h-full items-center justify-center text-sm text-slate-400">No state flow available.</div>;
    }

    return (
        <div className="space-y-3">
            <div>
                <div className="text-lg font-semibold text-slate-900">Setting State Flow</div>
                <div className="mt-1 text-sm text-slate-500">
                    Flow of pair states across the ordered settings, from equivalent to different outcomes.
                </div>
            </div>
            <div className="h-[420px] rounded-xl border border-slate-200 bg-white p-3">
                <ResponsiveContainer width="100%" height="100%">
                    <Sankey
                        data={data}
                        node={<CustomNode />}
                        nodePadding={28}
                        nodeWidth={18}
                        margin={{ top: 24, right: 24, bottom: 12, left: 24 }}
                        link={{ stroke: "#94a3b8", strokeOpacity: 0.35 }}
                    >
                        <Tooltip
                            formatter={(value: number) => [`${value} pairs`, "Flow"]}
                            contentStyle={{
                                borderRadius: 8,
                                border: "1px solid #dde1e7",
                                fontSize: 12,
                                boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
                            }}
                        />
                    </Sankey>
                </ResponsiveContainer>
            </div>
        </div>
    );
}
