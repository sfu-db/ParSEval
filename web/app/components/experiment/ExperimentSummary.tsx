
"use client";

import { KPICard } from "@/components/projects/KPICard";
import { computeMetrics } from "@/lib/utils";
import { EvalResult } from "@/lib/types";
import { useMemo } from "react";

interface Props {
    results: EvalResult[];
}

export function ExperimentSummary({ results }: Props) {
    const metrics = useMemo(() => {
        if (!results || results.length === 0) return null;
        return computeMetrics(results);
    }, [results]);

    if (!metrics) {
        return (
            <div className="text-sm text-gray-400">
                No evaluation results available. {results.length}
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="grid grid-cols-6 gap-4">
                <KPICard title="Datasets" value={metrics.numDatasets} />
                <KPICard title="Full Agreement" value={metrics.fullAgreement} />
                <KPICard title="Disagreement" value={metrics.disagreement} />
                <KPICard title="Settings" value={metrics.numSettings} />
                <KPICard title="Highest Setting" value={metrics.highestSetting ?? "N/A"} />
                <KPICard title="Lowest Setting" value={metrics.lowestSetting ?? "N/A"} />
            </div>
        </div>
    );
}