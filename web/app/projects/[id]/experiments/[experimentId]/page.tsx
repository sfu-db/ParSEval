"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";


import { ExperimentSummary } from "@/components/experiment/ExperimentSummary";

import { EvalResult } from "@/lib/types";

import { getExperimentById } from "@/lib/api/project";
import { getExecutionAccuracyByExperimentIdAndDataset, getExperimentSummary, getAllEvalResultByExpIdAndDataset } from "@/lib/api/eval";

import SettingRateBarChart from "@/components/experiment/SettingRateBarChart";
import SettingProfileRadar from "@/components/experiment/SettingProfileRadar";
import EquivalenceHeatmap from "@/components/experiment/EquivalenceHeatmap";
import PairConsistencyStrip from "@/components/experiment/PairConsistencyStrip";
import {
    Card,
    CardHeader,
    CardTitle,
    CardContent,
} from "@/components/ui/card";

export default function ExperimentDetail() {
    const [activeDataset, setActiveDataset] = useState<string | null>(null);
    const params = useParams();
    const experimentId = params?.id && !Array.isArray(params.id) ? Number(params.id) : undefined;
    const [datasets, setDatasets] = useState<string[]>([]);
    const [results, setResults] = useState<EvalResult[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        async function load() {
            if (!experimentId || isNaN(experimentId)) return;
            try {
                setLoading(true);
                setError(null);
                const exp = await getExperimentById(experimentId);
                if (!exp) throw new Error("Experiment not found");
                const datasetList: string[] = Array.isArray(exp.datasets) ? exp.datasets : [];
                setDatasets(datasetList);

                const allResults = await Promise.all(
                    datasetList.map(async (dataset) => {
                        const res =
                            await getAllEvalResultByExpIdAndDataset(
                                experimentId,
                                dataset
                            );

                        return res;
                    })
                );


                const flatResults = allResults.flat().filter(r => r && typeof r === 'object' && 'dataset' in r);
                setResults(flatResults as unknown as EvalResult[]);
            } catch (err) {
                console.error(err);
                setError("Failed to load experiment data");
            } finally {
                setLoading(false);
            }
        }
        load();
    }, [experimentId]);

    if (loading) {
        return <div className="p-6 text-gray-400">Loading...</div>;
    }
    if (error) {
        return <div className="p-6 text-red-400">{error}</div>;
    }

    return (
        <div className="p-6 space-y-6">
            <Card>
                <CardHeader>
                    <CardTitle>Experiment Summary</CardTitle>
                </CardHeader>
                <CardContent>
                    <ExperimentSummary results={results} />
                </CardContent>
            </Card>
            <Card>
                <CardContent>
                    <div className="flex flex-row gap-6 h-96">
                        <div className="flex-1 flex flex-col">
                            <div className="font-semibold text-lg mb-2 text-center">Execution Accuracy</div>
                            <div className="flex-1">
                                <SettingRateBarChart
                                    results={activeDataset ? results.filter(r => r.dataset === activeDataset) : results}
                                    onDrillDown={(info) => {
                                        console.log("Drill down:", info);
                                    }}
                                    activeDataset={activeDataset}
                                    onLegendClick={setActiveDataset}
                                />
                            </div>
                        </div>
                        <div className="flex-1 flex flex-col">
                            <div className="font-semibold text-lg mb-2 text-center"></div>
                            <div className="flex-1">
                                <SettingProfileRadar
                                    results={activeDataset ? results.filter(r => r.dataset === activeDataset) : results}
                                    activeDataset={activeDataset}
                                    onLegendClick={setActiveDataset}
                                />
                            </div>
                        </div>
                        <div className="flex-1 flex flex-col">
                            <div className="font-semibold text-lg mb-2 text-center">Equivalence Rate Heatmap</div>
                            <div className="flex-1">
                                <EquivalenceHeatmap
                                    results={activeDataset ? results.filter(r => r.dataset === activeDataset) : results}
                                    activeDataset={activeDataset}
                                    onLegendClick={setActiveDataset}
                                />
                            </div>
                        </div>
                    </div>
                </CardContent>
            </Card>
            <div className="space-y-2 text-sm">
                <PairConsistencyStrip results={results} />
            </div>

        </div>
    );
}