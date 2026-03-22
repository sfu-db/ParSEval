"use client";

import { Card, CardContent } from "@/components/ui/card";
import { ModelRun } from "@/lib/types";
import { cn, computeMetrics } from "@/lib/utils";
import { useMemo } from "react";

interface Props {
    projectName: string;
    modelRun: ModelRun;
    results: Array<{
        dataset: string;
        labels: Partial<Record<string, boolean>>;
    }>;
}

function formatPercent(value?: number) {
    if (typeof value !== "number" || Number.isNaN(value)) return "N/A";
    return `${Math.round(value * 100)}%`;
}

function formatStatus(status: string) {
    return status.charAt(0).toUpperCase() + status.slice(1);
}

function getStatusClasses(status: string) {
    switch (status.toLowerCase()) {
        case "completed":
            return "border-emerald-200 bg-emerald-50 text-emerald-700";
        case "running":
            return "border-sky-200 bg-sky-50 text-sky-700";
        case "pending":
            return "border-amber-200 bg-amber-50 text-amber-700";
        case "failed":
            return "border-rose-200 bg-rose-50 text-rose-700";
        default:
            return "border-slate-200 bg-slate-100 text-slate-700";
    }
}

function MetricTile({ title, value }: { title: string; value: string | number }) {
    return (
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{title}</div>
            <div className="mt-2 text-sm font-semibold text-slate-900">{value}</div>
        </div>
    );
}

export function ModelRunSummary({ projectName, modelRun, results }: Props) {
    const metrics = useMemo(() => {
        if (!results || results.length === 0) return null;
        return computeMetrics(results);
    }, [results]);

    return (
        <Card>

            <CardContent className="flex flex-wrap items-start justify-start gap-2 p-6">
                <div className="min-w-0 flex-[0.3] space-y-2">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
                        {projectName || "Project"}
                    </div>
                    <div>
                        <h1 className="text-2xl font-semibold text-slate-900">{modelRun.run || `Run ${modelRun.id}`}</h1>
                        <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-slate-600">
                            <p>
                                {modelRun.model} on   {modelRun.dataset}


                            </p>
                            <span
                                className={cn(
                                    "inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold",
                                    getStatusClasses(modelRun.status)
                                )}
                            >
                                {formatStatus(modelRun.status)}
                            </span>
                        </div>
                    </div>
                </div>

                <div className="grid min-w-[320px] flex-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
                    <MetricTile title="Execution Acc" value={formatPercent(modelRun.metric?.["EXEC ACC"])} />
                    <MetricTile title="Exact Match" value={formatPercent(modelRun.metric?.["EXACT MATCH"])} />
                    <MetricTile title="Full Agreement" value={metrics?.fullAgreement ?? "N/A"} />
                    <MetricTile title="Disagreement" value={metrics?.disagreement ?? "N/A"} />
                    <MetricTile title="Highest Setting" value={metrics?.highestSetting || "N/A"} />
                    <MetricTile title="Lowest Setting" value={metrics?.lowestSetting || "N/A"} />
                </div>

                {!metrics ? <div className="text-sm text-slate-400">No evaluation results available.</div> : null}
            </CardContent>
        </Card>
    );
}
