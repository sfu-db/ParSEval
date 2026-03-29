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
            return "border-emerald-300 bg-emerald-100 text-emerald-900";
        case "running":
            return "border-sky-300 bg-sky-100 text-sky-900";
        case "pending":
            return "border-amber-300 bg-amber-100 text-amber-900";
        case "failed":
            return "border-rose-300 bg-rose-100 text-rose-900";
        default:
            return "border-border bg-muted text-foreground";
    }
}

function MetricTile({ title, value }: { title: string; value: string | number }) {
    return (
        <div className="rounded-xl border border-border bg-background/75 px-4 py-3 shadow-[0_1px_0_rgba(15,23,42,0.04)]">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</div>
            <div className="mt-2 text-sm font-semibold text-foreground">{value}</div>
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
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                        {projectName || "Project"}
                    </div>
                    <div>
                        <h1 className="text-2xl font-semibold text-foreground">{modelRun.run || `Run ${modelRun.id}`}</h1>
                        <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-foreground/78">
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

                {!metrics ? <div className="text-sm text-muted-foreground">No evaluation results available.</div> : null}
            </CardContent>
        </Card>
    );
}
