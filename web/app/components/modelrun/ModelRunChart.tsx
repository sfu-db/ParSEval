"use client";

import { useState } from "react";

import SettingRateBarChart from "@/components/modelrun/SettingRateBarChart";
import { SettingConfusionHeatmap, type HeatmapSelection } from "@/components/modelrun/SettingConfusionHeatmap";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ModelRun, EvalRecord } from "@/lib/types";

interface Props {
    chartResults: EvalRecord[];
    heatmapResults: EvalRecord[];
    activeSeries: string | null;
    onSeriesChange: (dataset: string | null) => void;
    comparisonOptions: ModelRun[];
    compareRunId: number | null;
    compareRunLabel?: string;
    compareLoading: boolean;
    onCompareSelect: (runId: number | null) => void;
    selectedHeatmapCell: HeatmapSelection | null;
    onHeatmapCellSelect: (selection: HeatmapSelection | null) => void;
}

export function ModelRunChart({
    chartResults,
    heatmapResults,
    activeSeries,
    onSeriesChange,
    comparisonOptions,
    compareRunId,
    compareRunLabel,
    compareLoading,
    onCompareSelect,
    selectedHeatmapCell,
    onHeatmapCellSelect,
}: Props) {
    const [showComparePicker, setShowComparePicker] = useState(false);

    return (
        <Card>
            <CardContent className="p-6">
                <div className="grid gap-6 ">
                    <div className="grid gap-6 xl:grid-cols-[1.1fr_1fr] items-stretch">
                        <div className="flex h-full flex-col rounded-xl border border-border bg-background/80 p-4 shadow-[0_1px_0_rgba(15,23,42,0.03)]">
                            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                                <div>
                                    <div className="text-lg font-semibold text-foreground">Execution Accuracy</div>
                                    {compareRunLabel ? (
                                        <div className="mt-1 text-sm text-muted-foreground">Comparing against {compareRunLabel}</div>
                                    ) : (
                                        <div className="mt-1 text-sm text-muted-foreground">Compare this run against another model run in the project.</div>
                                    )}
                                </div>

                                <div className="flex flex-wrap items-center gap-2">
                                    <Button type="button" variant="outline" onClick={() => setShowComparePicker((value) => !value)}>
                                        Compare To
                                    </Button>
                                    {compareRunId ? (
                                        <Button type="button" variant="ghost" onClick={() => onCompareSelect(null)}>
                                            Clear
                                        </Button>
                                    ) : null}
                                </div>
                            </div>

                            {showComparePicker ? (
                                <div className="mb-4 flex flex-wrap items-center gap-2">
                                    <select
                                        className="h-9 min-w-[280px] rounded-lg border border-border bg-card px-3 text-sm text-foreground outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/10"
                                        value={compareRunId ?? ""}
                                        onChange={(event) => {
                                            const value = event.target.value;
                                            onCompareSelect(value ? Number(value) : null);
                                        }}
                                    >
                                        <option value="">Select a model run</option>
                                        {comparisonOptions.map((run) => (
                                            <option key={run.id} value={run.id}>
                                                {(run.run || `Run ${run.id}`)} | {run.model} | {run.dataset}
                                            </option>
                                        ))}
                                    </select>
                                    {compareLoading ? <span className="text-sm text-muted-foreground">Loading comparison...</span> : null}
                                </div>
                            ) : null}

                            <div className="h-[380px]">
                                <SettingRateBarChart
                                    results={chartResults}
                                    onDrillDown={(info) => {
                                        console.log("Drill down:", info);
                                    }}
                                    activeDataset={activeSeries}
                                    onLegendClick={onSeriesChange}
                                />
                            </div>
                        </div>

                        <div className="flex h-full flex-col rounded-xl border border-border bg-background/80 p-4 shadow-[0_1px_0_rgba(15,23,42,0.03)]">
                            <div className="flex-1 min-h-0">
                                <SettingConfusionHeatmap
                                    results={heatmapResults}
                                    selectedCell={selectedHeatmapCell}
                                    onCellSelect={onHeatmapCellSelect}
                                />

                            </div>

                        </div>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
