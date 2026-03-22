"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";

import PairConsistencyStrip from "@/components/modelrun/PairConsistencyStrip";
import { ModelRunChart } from "@/components/modelrun/ModelRunChart";
import { ModelRunSummary } from "@/components/modelrun/ModelRunSummary";
import { type HeatmapSelection } from "@/components/modelrun/SettingConfusionHeatmap";
import { EvalRecordAPI } from "@/lib/api/evalRecord";
import { ModelRunAPI } from "@/lib/api/modelRun";
import { ProjectAPI } from "@/lib/api/project";
import { EvalRecord, ModelRun } from "@/lib/types";

function runLabel(run: ModelRun) {
    return run.run || `Run ${run.id}`;
}

function relabelResults(results: EvalRecord[], label: string): EvalRecord[] {
    return results.map((result) => ({
        ...result,
        dataset: label,
    }));
}

export default function ModelRunDetailPage() {
    const params = useParams();
    const projectId = params?.id && !Array.isArray(params.id) ? Number(params.id) : undefined;
    const modelRunId = params?.modelRunId && !Array.isArray(params.modelRunId) ? Number(params.modelRunId) : undefined;

    const [projectName, setProjectName] = useState("");
    const [modelRun, setModelRun] = useState<ModelRun | null>(null);
    const [projectRuns, setProjectRuns] = useState<ModelRun[]>([]);
    const [results, setResults] = useState<EvalRecord[]>([]);
    const [activeSeries, setActiveSeries] = useState<string | null>(null);
    const [compareRunId, setCompareRunId] = useState<number | null>(null);
    const [compareRun, setCompareRun] = useState<ModelRun | null>(null);
    const [compareResults, setCompareResults] = useState<EvalRecord[]>([]);
    const [compareLoading, setCompareLoading] = useState(false);
    const [selectedHeatmapCell, setSelectedHeatmapCell] = useState<HeatmapSelection | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        async function load() {
            if (!projectId || Number.isNaN(projectId) || !modelRunId || Number.isNaN(modelRunId)) {
                setError("Invalid model run");
                setLoading(false);
                return;
            }

            try {
                setLoading(true);
                setError(null);

                const [project, run, records, runs] = await Promise.all([
                    ProjectAPI.getById(projectId),
                    ModelRunAPI.getById(modelRunId),
                    EvalRecordAPI.getByModelRunId(modelRunId),
                    ModelRunAPI.getAll(projectId),
                ]);

                if (!project || !run) {
                    throw new Error("Model run not found");
                }

                setProjectName(project.name);
                setModelRun(run);
                setProjectRuns(runs);
                setResults(records);
                setSelectedHeatmapCell(null);
            } catch (err) {
                console.error(err);
                setError("Failed to load model run data");
            } finally {
                setLoading(false);
            }
        }

        load();
    }, [modelRunId, projectId]);

    useEffect(() => {
        async function loadComparison() {
            if (!compareRunId) {
                setCompareRun(null);
                setCompareResults([]);
                setActiveSeries(null);
                return;
            }

            try {
                setCompareLoading(true);
                const [run, records] = await Promise.all([
                    ModelRunAPI.getById(compareRunId),
                    EvalRecordAPI.getByModelRunId(compareRunId),
                ]);

                if (!run) {
                    throw new Error("Comparison run not found");
                }

                setCompareRun(run);
                setCompareResults(records);
            } catch (err) {
                console.error(err);
                setCompareRun(null);
                setCompareResults([]);
            } finally {
                setCompareLoading(false);
            }
        }

        loadComparison();
    }, [compareRunId]);

    useEffect(() => {
        if (!selectedHeatmapCell) return;

        document.getElementById("pair-consistency-table")?.scrollIntoView({
            behavior: "smooth",
            block: "start",
        });
    }, [selectedHeatmapCell]);

    const comparisonOptions = useMemo(() => {
        if (!modelRun) return [];
        return projectRuns.filter((run) => run.id !== modelRun.id);
    }, [modelRun, projectRuns]);

    const chartResults = useMemo(() => {
        if (!modelRun) return [];

        const primary = relabelResults(results, runLabel(modelRun));
        if (!compareRun) return primary;

        return [...primary, ...relabelResults(compareResults, runLabel(compareRun))];
    }, [compareResults, compareRun, modelRun, results]);

    if (loading) {
        return <div className="p-6 text-gray-400">Loading...</div>;
    }

    if (error || !modelRun) {
        return <div className="p-6 text-red-400">{error ?? "Model run not found"}</div>;
    }

    return (
        <div className="space-y-6 p-6">
            <ModelRunSummary projectName={projectName} modelRun={modelRun} results={results} />
            <ModelRunChart
                chartResults={chartResults}
                heatmapResults={results}
                activeSeries={activeSeries}
                onSeriesChange={setActiveSeries}
                comparisonOptions={comparisonOptions}
                compareRunId={compareRunId}
                compareRunLabel={compareRun ? runLabel(compareRun) : undefined}
                compareLoading={compareLoading}
                onCompareSelect={setCompareRunId}
                selectedHeatmapCell={selectedHeatmapCell}
                onHeatmapCellSelect={setSelectedHeatmapCell}
            />
            <div id="pair-consistency-table" className="space-y-2 text-sm">
                <PairConsistencyStrip
                    results={results}
                    selectedHeatmapCell={selectedHeatmapCell}
                    onClearHeatmapSelection={() => setSelectedHeatmapCell(null)}
                />
            </div>
        </div>
    );
}
