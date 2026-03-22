"use client";

import { useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ModelRun } from "@/lib/types";
import { cn } from "@/lib/utils";
import { MODEL_DOT_COLORS, MODEL_LINE_COLORS } from "@/lib/settings";
interface Props {
    projectName: string;
    modelRuns: ModelRun[];
    compareIds: string[];
    onRunSelect: (run: ModelRun) => void;
}

type SortKey = "run" | "model" | "dataset" | "status" | "exact_match" | "execution_accuracy" | "createdAt";
type SortDirection = "asc" | "desc";
type ViewMode = "table" | "chart";


function formatMetric(value: unknown) {
    if (typeof value !== "number" || Number.isNaN(value)) {
        return "--";
    }

    const normalized = value <= 1 ? value * 100 : value;
    return `${normalized.toFixed(1)}%`;
}

function getMetricNumber(value: unknown) {
    if (typeof value !== "number" || Number.isNaN(value)) {
        return null;
    }

    return value <= 1 ? value * 100 : value;
}

function formatSecondsAgo(value: Date | string | undefined) {
    if (!value) {
        return "--";
    }

    const date = value instanceof Date ? value : new Date(value);
    const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));

    if (Number.isNaN(seconds)) {
        return "--";
    }

    if (seconds < 60) return `${seconds}s`;

    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m`;

    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h`;

    const days = Math.floor(hours / 24);
    if (days < 30) return `${days}d`;

    const months = Math.floor(days / 30);
    if (months < 12) return `${months}mo`;

    const years = Math.floor(days / 365);
    return `${years}y`;
}

function getStatusClasses(status: string | undefined) {
    switch (status?.toLowerCase()) {
        case "completed":
            return "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200";
        case "running":
            return "bg-amber-50 text-amber-700 ring-1 ring-amber-200";
        case "pending":
            return "bg-sky-50 text-sky-700 ring-1 ring-sky-200";
        case "failed":
            return "bg-rose-50 text-rose-700 ring-1 ring-rose-200";
        default:
            return "bg-slate-100 text-slate-600 ring-1 ring-slate-200";
    }
}

function getSortValue(run: ModelRun, key: SortKey) {
    switch (key) {
        case "run":
            return run.run || `run-${run.id}`;
        case "model":
            return run.model || "";
        case "dataset":
            return run.dataset || "";
        case "status":
            return run.status || "";
        case "exact_match":
            return getMetricNumber(run.metric?.["EXACT MATCH"]) ?? -1;
        case "execution_accuracy":
            return getMetricNumber(run.metric?.["EXEC ACC"]) ?? -1;
        case "createdAt":
            return new Date(run.createdAt).getTime();
        default:
            return "";
    }
}

function SortHeader({
    label,
    column,
    sortKey,
    sortDirection,
    onSort,
}: {
    label: string;
    column: SortKey;
    sortKey: SortKey;
    sortDirection: SortDirection;
    onSort: (column: SortKey) => void;
}) {
    const active = sortKey === column;

    return (
        <button
            type="button"
            onClick={() => onSort(column)}
            className={cn(
                "inline-flex items-center gap-1 font-medium transition hover:text-slate-700",
                active ? "text-slate-800" : "text-slate-500"
            )}
        >
            <span>{label}</span>
            <span className="text-[10px]">{active ? (sortDirection === "asc" ? "▲" : "▼") : "↕"}</span>
        </button>
    );
}

function ViewModeToggle({ viewMode, onChange }: { viewMode: ViewMode; onChange: (mode: ViewMode) => void }) {
    return (
        <div className="inline-flex items-center rounded-md bg-white p-1 py-1.5">
            <button
                type="button"
                onClick={() => onChange("table")}
                className={cn(
                    "inline-flex size-9 items-center justify-center rounded text-slate-500 transition cursor-pointer hover:opacity-80 ",
                    viewMode === "table" && "bg-slate-100 text-slate-900 border"
                )}
                title="Table view"
                aria-label="Table view"
            >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="size-4" aria-hidden="true">
                    <rect x="3" y="5" width="18" height="14" rx="1.5" />
                    <path d="M3 10h18M8 5v14M16 5v14" />
                </svg>
            </button>
            <button
                type="button"
                onClick={() => onChange("chart")}
                className={cn(
                    "inline-flex size-9 items-center justify-center rounded text-slate-500 transition cursor-pointer hover:opacity-80",
                    viewMode === "chart" && "bg-slate-100 text-slate-900 border"
                )}
                title="Chart view"
                aria-label="Chart view"
            >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="size-4" aria-hidden="true">
                    <path d="M4 19V5" />
                    <path d="M4 19h16" />
                    <path d="M7 15l3-4 3 2 4-6" strokeLinecap="round" strokeLinejoin="round" />
                    <circle cx="7" cy="15" r="1" fill="currentColor" stroke="none" />
                    <circle cx="10" cy="11" r="1" fill="currentColor" stroke="none" />
                    <circle cx="13" cy="13" r="1" fill="currentColor" stroke="none" />
                    <circle cx="17" cy="7" r="1" fill="currentColor" stroke="none" />
                </svg>
            </button>
        </div>
    );
}

export default function ModelRunComparision({ projectName, modelRuns, compareIds, onRunSelect }: Props) {
    const [selectedDataset, setSelectedDataset] = useState<string>("all");
    const [selectedModel, setSelectedModel] = useState<string>("all");
    const [minExecAccuracyInput, setMinExecAccuracyInput] = useState<string>("");
    const [sortKey, setSortKey] = useState<SortKey>("execution_accuracy");
    const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
    const [viewMode, setViewMode] = useState<ViewMode>("table");
    const [chartDataset, setChartDataset] = useState<string>("all");
    const [promptDetail, setPromptDetail] = useState<{ runName: string; prompt: string } | null>(null);

    const selectedRuns = useMemo(() => {
        const compareSet = new Set(compareIds);
        return modelRuns.filter((run) => compareSet.has(String(run.id)));
    }, [compareIds, modelRuns]);

    const datasetOptions = useMemo(() => {
        return Array.from(new Set(selectedRuns.map((run) => run.dataset).filter(Boolean))).sort();
    }, [selectedRuns]);

    const effectiveChartDataset = datasetOptions.includes(chartDataset) ? chartDataset : (datasetOptions[0] ?? "all");


    const modelOptions = useMemo(() => {
        return Array.from(new Set(selectedRuns.map((run) => run.model).filter(Boolean))).sort();
    }, [selectedRuns]);

    const modelDotColorMap = useMemo(() => {
        const uniqueModels = Array.from(new Set(selectedRuns.map((run) => run.model).filter(Boolean))).sort();
        return Object.fromEntries(
            uniqueModels.map((model, index) => [model, MODEL_DOT_COLORS[index % MODEL_DOT_COLORS.length]])
        ) as Record<string, string>;
    }, [selectedRuns]);

    const modelLineColorMap = useMemo(() => {
        const uniqueModels = Array.from(new Set(selectedRuns.map((run) => run.model).filter(Boolean))).sort();
        return Object.fromEntries(
            uniqueModels.map((model, index) => [model, MODEL_LINE_COLORS[index % MODEL_LINE_COLORS.length]])
        ) as Record<string, string>;
    }, [selectedRuns]);

    const minExecAccuracy = useMemo(() => {
        if (!minExecAccuracyInput.trim()) return null;
        const parsed = Number(minExecAccuracyInput);
        return Number.isFinite(parsed) ? parsed : null;
    }, [minExecAccuracyInput]);

    const filteredRuns = useMemo(() => {
        return selectedRuns.filter((run) => {
            if (selectedDataset !== "all" && run.dataset !== selectedDataset) {
                return false;
            }

            if (selectedModel !== "all" && run.model !== selectedModel) {
                return false;
            }

            if (minExecAccuracy !== null) {
                const execAcc = getMetricNumber(run.metric?.["EXEC ACC"]);
                if (execAcc === null || execAcc < minExecAccuracy) {
                    return false;
                }
            }

            return true;
        });
    }, [selectedRuns, selectedDataset, selectedModel, minExecAccuracy]);

    const displayedRuns = useMemo(() => {
        return [...filteredRuns].sort((a, b) => {
            const left = getSortValue(a, sortKey);
            const right = getSortValue(b, sortKey);

            if (typeof left === "number" && typeof right === "number") {
                return sortDirection === "asc" ? left - right : right - left;
            }

            const result = String(left).localeCompare(String(right));
            return sortDirection === "asc" ? result : -result;
        });
    }, [filteredRuns, sortDirection, sortKey]);

    const chartRuns = useMemo(() => {
        if (effectiveChartDataset === "all") {
            return [];
        }

        return selectedRuns.filter((run) => {
            if (run.dataset !== effectiveChartDataset) {
                return false;
            }

            if (selectedModel !== "all" && run.model !== selectedModel) {
                return false;
            }

            if (minExecAccuracy !== null) {
                const execAcc = getMetricNumber(run.metric?.["EXEC ACC"]);
                if (execAcc === null || execAcc < minExecAccuracy) {
                    return false;
                }
            }

            return true;
        });
    }, [effectiveChartDataset, minExecAccuracy, selectedModel, selectedRuns]);

    const chartModelKeys = useMemo(() => {
        return Array.from(new Set(chartRuns.map((run) => run.model).filter(Boolean))).sort();
    }, [chartRuns]);

    const chartData = useMemo(() => {
        const grouped = new Map<string, Record<string, string | number | null>>();

        for (const run of chartRuns) {
            const runName = run.run || `run-${run.id}`;
            const modelName = run.model || "Unknown model";
            const execAcc = getMetricNumber(run.metric?.["EXEC ACC"]);
            const existing = grouped.get(runName) ?? { name: runName, createdAt: new Date(run.createdAt).getTime() };
            existing[modelName] = execAcc;
            existing.createdAt = Math.min(Number(existing.createdAt), new Date(run.createdAt).getTime());
            grouped.set(runName, existing);
        }

        return Array.from(grouped.values())
            .sort((left, right) => Number(left.createdAt) - Number(right.createdAt))
            .map((row) => {
                const { createdAt, ...rest } = row;
                void createdAt;
                return rest;
            });
    }, [chartRuns]);

    const datasets = useMemo(() => {
        return new Set(selectedRuns.map((run) => run.dataset).filter(Boolean)).size;
    }, [selectedRuns]);

    const bestRun = useMemo(() => {
        return [...selectedRuns].sort((a, b) => {
            const aScore = typeof a.metric?.["EXEC ACC"] === "number" ? a.metric["EXEC ACC"] : -1;
            const bScore = typeof b.metric?.["EXEC ACC"] === "number" ? b.metric["EXEC ACC"] : -1;
            return bScore - aScore;
        })[0] ?? null;
    }, [selectedRuns]);

    function handleSort(column: SortKey) {
        if (sortKey === column) {
            setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
            return;
        }

        setSortKey(column);
        setSortDirection(column === "run" || column === "model" || column === "dataset" || column === "status" ? "asc" : "desc");
    }

    return (
        <div className="flex flex-col gap-6">
            <section className="rounded-2xl border border-slate-200 bg-white">
                <div className="border-b border-slate-200 px-6 py-3.5">
                    <h1 className="text-xl font-semibold text-slate-900">{projectName}</h1>
                    <p className="mt-0.5 text-sm text-slate-600">
                        Comparing {selectedRuns.length} selected run{selectedRuns.length === 1 ? "" : "s"} from the sidebar.
                    </p>
                </div>

                <div className="grid gap-4 p-6 md:grid-cols-3">
                    <Card className="gap-0 border-slate-200 py-0 shadow-none">
                        <CardContent className="px-4 py-4">
                            <p className="text-xs font-medium uppercase tracking-[0.16em] text-slate-500">Active Datasets</p>
                            <p className="mt-2 text-3xl font-semibold text-slate-900">{datasets}</p>
                        </CardContent>
                    </Card>
                    <Card className="gap-0 border-slate-200 py-0 shadow-none">
                        <CardContent className="px-4 py-4">
                            <p className="text-xs font-medium uppercase tracking-[0.16em] text-slate-500">Best Accuracy</p>
                            <p className="mt-2 text-3xl font-semibold text-slate-900">
                                {bestRun ? formatMetric(bestRun.metric?.["EXEC ACC"]) : "--"}
                            </p>
                        </CardContent>
                    </Card>
                    <Card className="gap-0 border-slate-200 py-0 shadow-none">
                        <CardContent className="px-4 py-4">
                            <p className="text-xs font-medium uppercase tracking-[0.16em] text-slate-500">Best Run</p>
                            <p className="mt-2 truncate text-lg font-semibold text-slate-900">
                                {bestRun ? bestRun.run || `run-${bestRun.id}` : "--"}
                            </p>
                            {bestRun ? <p className="mt-1 text-sm text-slate-500">{bestRun.model}</p> : null}
                        </CardContent>
                    </Card>
                </div>
            </section>

            <Card className="gap-0 border-slate-200 bg-white py-0 shadow-none">
                <CardHeader className="border-b border-slate-200 py-5">
                    <CardTitle className="text-slate-900">Selected Run Details</CardTitle>
                    <CardDescription>
                        Detailed metadata for each run currently selected in the sidebar.
                    </CardDescription>
                </CardHeader>
                <CardContent className="px-0 py-0">
                    {selectedRuns.length === 0 ? (
                        <div className="px-6 py-16 text-center text-sm text-slate-500">
                            No runs selected yet. Use the eye controls in the sidebar to hide runs you want to ignore.
                        </div>
                    ) : (
                        <>
                            <div className="flex flex-col gap-3 border-b border-slate-200 px-6 py-4 lg:flex-row lg:items-end lg:justify-between">
                                <div className="grid gap-3 lg:grid-cols-[auto_minmax(0,3fr)_minmax(220px,1fr)_minmax(220px,1fr)] lg:items-end">
                                    <div className="flex flex-col gap-1.5 text-sm text-slate-600">
                                        <ViewModeToggle viewMode={viewMode} onChange={setViewMode} />
                                    </div>

                                    <label className="flex flex-col gap-1.5 text-sm text-slate-600">
                                        <span className="text-xs font-medium uppercase tracking-[0.14em] text-slate-500">Min Exec Acc</span>
                                        <div className="flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-1.5 focus-within:border-slate-300">
                                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="size-4 shrink-0 text-slate-400" aria-hidden="true">
                                                <circle cx="11" cy="11" r="6.5" />
                                                <path d="M16 16l4 4" strokeLinecap="round" />
                                            </svg>
                                            <input
                                                type="number"
                                                min="0"
                                                max="100"
                                                step="0.1"
                                                value={minExecAccuracyInput}
                                                onChange={(event) => setMinExecAccuracyInput(event.target.value)}
                                                placeholder="Filter by execution accuracy, e.g. 80"
                                                className="min-w-0 flex-1 bg-transparent text-sm text-slate-900 outline-none placeholder:text-slate-400"
                                            />
                                            <span
                                                className="inline-flex size-5 shrink-0 items-center justify-center rounded-full border border-slate-200 text-[11px] font-medium text-slate-400"
                                                title="Only show runs whose execution accuracy is greater than or equal to this value."
                                                aria-label="Execution accuracy filter help"
                                            >
                                                ?
                                            </span>
                                        </div>
                                    </label>

                                    {viewMode === "table" ? (
                                        <label className="flex flex-col gap-1.5 text-sm text-slate-600">
                                            <span className="text-xs font-medium uppercase tracking-[0.14em] text-slate-500">Dataset</span>
                                            <select
                                                value={selectedDataset}
                                                onChange={(event) => setSelectedDataset(event.target.value)}
                                                className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-900 outline-none focus:border-slate-300"
                                            >
                                                <option value="all">All datasets</option>
                                                {datasetOptions.map((dataset) => (
                                                    <option key={dataset} value={dataset}>
                                                        {dataset}
                                                    </option>
                                                ))}
                                            </select>
                                        </label>
                                    ) : null}

                                    <label className="flex flex-col gap-1.5 text-sm text-slate-600">
                                        <span className="text-xs font-medium uppercase tracking-[0.14em] text-slate-500">Model</span>
                                        <select
                                            value={selectedModel}
                                            onChange={(event) => setSelectedModel(event.target.value)}
                                            className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-900 outline-none focus:border-slate-300"
                                        >
                                            <option value="all">All models</option>
                                            {modelOptions.map((model) => (
                                                <option key={model} value={model}>
                                                    {model}
                                                </option>
                                            ))}
                                        </select>
                                    </label>
                                </div>

                                <div className="flex flex-col items-start gap-1 text-sm text-slate-500 lg:items-end">
                                    {viewMode === "chart" ? (
                                        <label className="flex items-center gap-2 text-sm text-slate-600">
                                            <span className="text-xs font-medium uppercase tracking-[0.14em] text-slate-500">Trend Dataset</span>
                                            <select
                                                value={effectiveChartDataset}
                                                onChange={(event) => setChartDataset(event.target.value)}
                                                className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-900 outline-none focus:border-slate-300"
                                            >
                                                {datasetOptions.map((dataset) => (
                                                    <option key={dataset} value={dataset}>
                                                        {dataset}
                                                    </option>
                                                ))}
                                            </select>
                                        </label>
                                    ) : null}
                                    <p>Showing {displayedRuns.length} of {selectedRuns.length} runs</p>
                                </div>
                            </div>

                            {displayedRuns.length === 0 ? (
                                <div className="px-6 py-16 text-center text-sm text-slate-500">
                                    No runs match the current filters.
                                </div>
                            ) : viewMode === "chart" ? (
                                <div className="px-6 py-6">
                                    {!datasetOptions.length ? (
                                        <div className="py-12 text-center text-sm text-slate-500">
                                            No dataset is available for the selected runs.
                                        </div>
                                    ) : !chartData.length ? (
                                        <div className="py-12 text-center text-sm text-slate-500">
                                            No execution-accuracy trend data is available for dataset <span className="font-medium text-slate-700">{effectiveChartDataset}</span>.
                                        </div>
                                    ) : (
                                        <>
                                            <div className="mb-4 text-sm text-slate-500">
                                                X-axis shows run names. Each line tracks execution accuracy for one model on <span className="font-medium text-slate-700">{effectiveChartDataset}</span>.
                                            </div>
                                            <div className="h-[420px] w-full">
                                                <ResponsiveContainer width="100%" height="100%">
                                                    <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 24, left: 0 }}>
                                                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                                                        <XAxis dataKey="name" tick={{ fill: "#64748b", fontSize: 12 }} angle={-20} textAnchor="end" height={64} interval={0} />
                                                        <YAxis domain={[0, 100]} tick={{ fill: "#64748b", fontSize: 12 }} />
                                                        <Tooltip
                                                            formatter={(value: number | null, name: string) => {
                                                                if (typeof value !== "number" || Number.isNaN(value)) return ["--", name];
                                                                return [`${value.toFixed(1)}%`, name];
                                                            }}
                                                            labelFormatter={(label: string) => `${label} | ${effectiveChartDataset}`}
                                                        />
                                                        <Legend wrapperStyle={{ paddingTop: 8 }} />
                                                        {chartModelKeys.map((model, index) => (
                                                            <Line
                                                                key={model}
                                                                type="monotone"
                                                                dataKey={model}
                                                                name={model}
                                                                stroke={modelLineColorMap[model] || MODEL_LINE_COLORS[index % MODEL_LINE_COLORS.length]}
                                                                strokeWidth={2.5}
                                                                dot={{ r: 3 }}
                                                                activeDot={{ r: 5 }}
                                                                connectNulls
                                                            />
                                                        ))}
                                                    </LineChart>
                                                </ResponsiveContainer>
                                            </div>
                                        </>
                                    )}
                                </div>
                            ) : (
                                <div className="overflow-x-auto">
                                    <table className="min-w-full text-sm">
                                        <thead className="bg-slate-50 text-left text-xs uppercase tracking-[0.16em] text-slate-500">
                                            <tr>
                                                <th className="px-6 py-4 font-medium"><SortHeader label="Run" column="run" sortKey={sortKey} sortDirection={sortDirection} onSort={handleSort} /></th>
                                                <th className="px-6 py-4 font-medium"><SortHeader label="Model" column="model" sortKey={sortKey} sortDirection={sortDirection} onSort={handleSort} /></th>
                                                <th className="px-6 py-4 font-medium"><SortHeader label="Dataset" column="dataset" sortKey={sortKey} sortDirection={sortDirection} onSort={handleSort} /></th>
                                                <th className="px-6 py-4 font-medium"><SortHeader label="Status" column="status" sortKey={sortKey} sortDirection={sortDirection} onSort={handleSort} /></th>
                                                <th className="px-6 py-4 font-medium"><SortHeader label="Exact Match" column="exact_match" sortKey={sortKey} sortDirection={sortDirection} onSort={handleSort} /></th>
                                                <th className="px-6 py-4 font-medium"><SortHeader label="Exec Acc" column="execution_accuracy" sortKey={sortKey} sortDirection={sortDirection} onSort={handleSort} /></th>
                                                <th className="px-6 py-4 font-medium"><SortHeader label="CreateSecsAgo" column="createdAt" sortKey={sortKey} sortDirection={sortDirection} onSort={handleSort} /></th>
                                                <th className="px-6 py-4 font-medium">PromptTemplate</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {displayedRuns.map((run) => (
                                                <tr
                                                    key={run.id}
                                                    className="cursor-pointer border-t border-slate-100 align-top transition hover:bg-slate-50"
                                                    onClick={() => onRunSelect(run)}
                                                >
                                                    <td className="px-6 py-4 font-medium text-slate-900">
                                                        <span className="truncate text-left text-sky-700 transition hover:text-sky-900 hover:underline">
                                                            {run.run || `run-${run.id}`}
                                                        </span>
                                                    </td>
                                                    <td className="px-6 py-4 text-slate-700">
                                                        <span className="inline-flex items-center gap-2">
                                                            <span className={cn("size-2.5 rounded-full", modelDotColorMap[run.model] || MODEL_DOT_COLORS[0])} />
                                                            <span>{run.model}</span>
                                                        </span>
                                                    </td>
                                                    <td className="px-6 py-4 text-slate-700">{run.dataset}</td>
                                                    <td className="px-6 py-4">
                                                        <span className={cn("inline-flex rounded-full px-2.5 py-1 text-xs font-medium capitalize", getStatusClasses(run.status))}>
                                                            {run.status || "unknown"}
                                                        </span>
                                                    </td>
                                                    <td className="px-6 py-4 text-slate-700">{formatMetric(run.metric?.["EXACT MATCH"])}</td>
                                                    <td className="px-6 py-4 text-slate-700">{formatMetric(run.metric?.["EXEC ACC"])}</td>
                                                    <td className="px-6 py-4 text-slate-500">{formatSecondsAgo(run.createdAt)}</td>
                                                    <td className="max-w-xs px-6 py-4 text-slate-500">
                                                        <button
                                                            type="button"
                                                            onClick={(event) => {
                                                                event.stopPropagation();
                                                                setPromptDetail({
                                                                    runName: run.run || `run-${run.id}`,
                                                                    prompt: run.promptTemplate || "No prompt template available.",
                                                                });
                                                            }}
                                                            className="block truncate text-left text-sky-700 transition hover:text-sky-900 hover:underline cursor-pointer hover:opacity-80"
                                                        >
                                                            {run.promptTemplate || "--"}
                                                        </button>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </>
                    )}
                </CardContent>
            </Card>

            {promptDetail ? (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 p-6">
                    <div className="w-full max-w-3xl rounded-2xl border border-slate-200 bg-white shadow-xl">
                        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-4">
                            <div>
                                <h2 className="text-lg font-semibold text-slate-900">Prompt Template</h2>
                                <p className="mt-1 text-sm text-slate-500">{promptDetail.runName}</p>
                            </div>
                            <button
                                type="button"
                                onClick={() => setPromptDetail(null)}
                                className="rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-600 transition hover:bg-slate-50"
                            >
                                Close
                            </button>
                        </div>
                        <div className="max-h-[70vh] overflow-y-auto px-6 py-5">
                            <pre className="whitespace-pre-wrap break-words text-sm leading-6 text-slate-700">
                                {promptDetail.prompt}
                            </pre>
                        </div>
                    </div>
                </div>
            ) : null}
        </div>
    );
}
