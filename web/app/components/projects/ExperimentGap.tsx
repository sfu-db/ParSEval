"use client";

import { useEffect, useState, useMemo } from "react";
import { ExperimentResult } from "@/lib/types";
import { getExecutionAccuracyByExperimentIdAndDataset } from "@/lib/api/evalRecord";
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
import { ChartWrapper } from "@/components/modelrun/ChartWrapper";

import {
    Card,
    CardHeader,
    CardTitle,
    CardContent,
} from "@/components/ui/card";

interface Props {
    experiments: ExperimentResult[];
}

type ChartDatum = {
    name: string;
    maxAcc: number;
    [dataset: string]: string | number;
};

export default function ExperimentTrends({ experiments }: Props) {
    const datasets = useMemo(
        () => getDatasetsFromExperiments(experiments),
        [experiments]
    );
    const [chartData, setChartData] = useState<ChartDatum[]>([]);
    const numExperiments = experiments.length;
    const numDatasets = datasets.length;
    const [bestAccuracy, setBestAccuracy] = useState<number>(0);
    const [selectedDataset, setSelectedDataset] = useState<string | null>(null);

    useEffect(() => {
        if (!experiments.length || !datasets.length) return;

        async function load() {
            const data = await Promise.all(
                experiments.map(async (exp) => {
                    const entry: ChartDatum = { name: exp.name, maxAcc: 0 };
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
            <div className="mb-2 text-sm text-muted-foreground">
                <span>Datasets: </span>
                {datasets.length === 0 ? (
                    <span className="text-muted-foreground">None</span>
                ) : (
                    datasets.map((ds) => (
                        <span
                            key={ds}
                            className={`mr-2 rounded px-2 py-1 text-xs transition ${selectedDataset === ds ? "bg-primary/15 text-primary font-bold" : "bg-muted text-foreground/80 hover:bg-accent"}`}
                            title={`Click to show only ${ds}`}
                            onClick={() => setSelectedDataset(selectedDataset === ds ? null : ds)}
                            style={{ cursor: 'pointer' }}
                        >
                            {ds}
                        </span>
                    ))
                )}
                {selectedDataset && (
                    <span className="ml-2 text-xs text-primary">Showing only <b>{selectedDataset}</b></span>
                )}
            </div>

            {/* Chart */}
            <Card className="border-border shadow-sm">
                <CardHeader>
                    <CardTitle className="text-xl text-foreground">Experiment vs Dataset Accuracy</CardTitle>
                </CardHeader>
                <CardContent>
                    <ChartWrapper height={384}>
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={chartData}>
                                <CartesianGrid strokeDasharray="3 3" />
                                <XAxis dataKey="name" />
                                <YAxis domain={[0, 100]} />
                                <Tooltip />
                                <Legend
                                    onClick={(e: { dataKey?: string } | undefined) => {
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
                    </ChartWrapper>
                </CardContent>
            </Card>
        </div>
    );
}
