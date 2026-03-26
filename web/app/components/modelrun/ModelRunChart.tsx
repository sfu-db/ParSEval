"use client";

import { useState } from "react";

import SettingRateBarChart from "@/components/modelrun/SettingRateBarChart";
import { SettingConfusionHeatmap, type HeatmapSelection } from "@/components/modelrun/SettingConfusionHeatmap";
import { SettingStateSankey } from "@/components/modelrun/SettingStateSankey";
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
                        <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 h-full flex flex-col">
                            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                                <div>
                                    <div className="text-lg font-semibold text-slate-900">Execution Accuracy</div>
                                    {compareRunLabel ? (
                                        <div className="mt-1 text-sm text-slate-500">Comparing against {compareRunLabel}</div>
                                    ) : (
                                        <div className="mt-1 text-sm text-slate-500">Compare this run against another model run in the project.</div>
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
                                        className="h-9 min-w-[280px] rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none transition focus:border-slate-400"
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
                                    {compareLoading ? <span className="text-sm text-slate-500">Loading comparison...</span> : null}
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

                        <div className="flex flex-col rounded-xl border border-slate-200 bg-slate-50 p-4 h-full">
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
