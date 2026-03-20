"use client";

import { useEffect, useState, useMemo } from "react";
import { ExperimentResult } from "@/lib/types";
import { getExecutionAccuracyByExperimentIdAndDataset } from "@/lib/api/eval";
import { getDatasetsFromExperiments } from "@/lib/utils";

import {
    BarChart,
    Bar,
    XAxis,
    YAxis,
    Tooltip,
    ResponsiveContainer,
    CartesianGrid,
    Legend,
} from "recharts";

import { KPICard } from "@/components/projects/KPICard";

import {
    Card,
    CardHeader,
    CardTitle,
    CardContent,
} from "@/components/ui/card";

interface Props {
    experiments: ExperimentResult[];
}

export default function ExperimentTrends({ experiments }: Props) {
    const datasets = useMemo(
        () => getDatasetsFromExperiments(experiments),
        [experiments]
    );
    const [chartData, setChartData] = useState<any[]>([]);
    const numExperiments = experiments.length;
    const numDatasets = datasets.length;
    const [bestAccuracy, setBestAccuracy] = useState<number>(0);
    const [selectedDataset, setSelectedDataset] = useState<string | null>(null);

    useEffect(() => {
        if (!experiments.length || !datasets.length) return;

        async function load() {
            const data = await Promise.all(
                experiments.map(async (exp) => {
                    const entry: any = { name: exp.name };
                    let maxAcc = 0;
                    await Promise.all(
                        datasets.map(async (dataset) => {
                            const acc = await getExecutionAccuracyByExperimentIdAndDataset(
                                exp.id,
                                dataset
                            );
                            entry[dataset] = (acc || 0) * 100;
                            if ((acc || 0) > maxAcc) maxAcc = acc || 0;
                        })
                    );
                    entry.maxAcc = maxAcc * 100;
                    return entry;
                })
            );
            setChartData(data);
            // Find best accuracy across all experiments
            const best = Math.max(...data.map(d => d.maxAcc ?? 0));
            setBestAccuracy(best);
        }
        load();
    }, [experiments, datasets]);

    if (!experiments.length) {
        return (
            <div className="text-muted-foreground p-4 text-center text-lg">
                No experiment data available.
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="grid grid-cols-3 gap-4">
                <KPICard title="Experiments" value={numExperiments} />
                <KPICard title="Datasets" value={numDatasets} />
                <KPICard title="Best Accuracy" value={`${bestAccuracy.toFixed(2)}%`} />
            </div>

            {/* Dataset Info */}
            <div className="text-sm text-gray-400 mb-2">
                <span>Datasets: </span>
                {datasets.length === 0 ? (
                    <span className="text-gray-500">None</span>
                ) : (
                    datasets.map((ds, idx) => (
                        <span
                            key={ds}
                            className={`px-2 py-1 bg-gray-100 rounded text-xs text-gray-700 mr-2 cursor-pointer ${selectedDataset === ds ? "bg-blue-200 text-blue-700 font-bold" : ""}`}
                            title={`Click to show only ${ds}`}
                            onClick={() => setSelectedDataset(selectedDataset === ds ? null : ds)}
                            style={{ cursor: 'pointer' }}
                        >
                            {ds}
                        </span>
                    ))
                )}
                {selectedDataset && (
                    <span className="ml-2 text-xs text-blue-700">Showing only <b>{selectedDataset}</b></span>
                )}
            </div>

            {/* Chart */}
            <Card className="shadow-lg border border-blue-100">
                <CardHeader>
                    <CardTitle className="text-blue-700 text-xl">Experiment vs Dataset Accuracy</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="w-full h-96">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={chartData}>
                                <CartesianGrid strokeDasharray="3 3" />
                                <XAxis dataKey="name" />
                                <YAxis domain={[0, 100]} />
                                <Tooltip />
                                <Legend
                                    onClick={(e: any) => {
                                        const ds = e && e.dataKey;
                                        if (ds && datasets.includes(ds)) {
                                            setSelectedDataset(selectedDataset === ds ? null : ds);
                                        }
                                    }}
                                />
                                {(selectedDataset
                                    ? datasets.filter(ds => ds === selectedDataset)
                                    : datasets
                                ).map((dataset, idx) => (
                                    <Bar
                                        key={dataset}
                                        dataKey={dataset}
                                        fill={[
                                            "#6366f1",
                                            "#22c55e",
                                            "#f59e0b",
                                            "#ef4444",
                                            "#14b8a6",
                                        ][idx % 5]}
                                        name={dataset}
                                    />
                                ))}
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}