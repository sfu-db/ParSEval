"use client";

import { useMemo, useState } from "react";

import { ModelRun } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

interface Props {
    modelRuns: ModelRun[];
    selectedId: string | null;
    onSelect: (exp: ModelRun) => void;
    onUpload: () => void;
    onDelete: (run: ModelRun) => void;
    compareIds?: string[];
    onCompareChange?: (ids: string[]) => void;
}

function EyeIcon({ visible }: { visible: boolean }) {
    if (!visible) {
        return (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="size-4" aria-hidden="true">
                <path d="M3 3l18 18" strokeLinecap="round" />
                <path d="M10.6 10.7a2 2 0 0 0 2.7 2.7" strokeLinecap="round" />
                <path d="M9.4 5.5A11 11 0 0 1 12 5.2c5.5 0 9.3 4.6 10 5.5-.4.5-1.6 1.9-3.4 3.2" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M6.2 6.2C3.8 7.7 2.3 9.8 2 10.2c.7.9 4.5 5.5 10 5.5 1.1 0 2.1-.1 3-.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
        );
    }

    return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="size-4" aria-hidden="true">
            <path d="M2 12s3.6-6.8 10-6.8S22 12 22 12s-3.6 6.8-10 6.8S2 12 2 12Z" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="12" cy="12" r="3" />
        </svg>
    );
}

function TrashIcon() {
    return (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="size-4" aria-hidden="true">
            <path d="M4 7h16" strokeLinecap="round" />
            <path d="M10 11v6" strokeLinecap="round" />
            <path d="M14 11v6" strokeLinecap="round" />
            <path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
    );
}

export default function ModelRunSidebar({
    modelRuns,
    selectedId,
    onSelect,
    onUpload,
    onDelete,
    compareIds,
    onCompareChange,
}: Props) {
    const [query, setQuery] = useState("");
    const [internalCompareIds, setInternalCompareIds] = useState<string[]>([]);

    const activeCompareIds = compareIds ?? (internalCompareIds.length > 0 ? internalCompareIds : modelRuns.map((run) => String(run.id)));

    const filteredRuns = useMemo(() => {
        const normalizedQuery = query.trim().toLowerCase();
        if (!normalizedQuery) {
            return modelRuns;
        }

        return modelRuns.filter((run) =>
            [run.run, run.model, String(run.id)]
                .filter(Boolean)
                .some((value) => String(value).toLowerCase().includes(normalizedQuery))
        );
    }, [modelRuns, query]);

    function updateCompareIds(nextIds: string[]) {
        if (compareIds === undefined) {
            setInternalCompareIds(nextIds);
        }
        onCompareChange?.(nextIds);
    }

    function toggleVisibility(runId: string) {
        const exists = activeCompareIds.includes(runId);
        if (exists) {
            updateCompareIds(activeCompareIds.filter((id) => id !== runId));
            return;
        }

        updateCompareIds([...activeCompareIds, runId]);
    }

    return (
        <aside className="flex h-full w-full max-w-sm flex-col border-r border-slate-200 bg-slate-50 xl:max-w-md">
            <div className="border-b border-slate-200 bg-white px-5 py-5">
                <div className="mb-4 flex items-center justify-between gap-3">
                    <div>
                        <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-500">
                            Model Runs
                        </p>
                        <p className="mt-1 text-sm text-slate-600">
                            {activeCompareIds.length} of {modelRuns.length} visible
                        </p>
                    </div>
                    <Button onClick={onUpload} size="sm" className="px-3">
                        Upload
                    </Button>
                </div>

                <div className="rounded-md border border-slate-200 bg-white px-3 py-2">
                    <input
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="Search model runs..."
                        className="w-full bg-transparent text-sm text-slate-700 outline-none placeholder:text-slate-400"
                    />
                </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto bg-white">
                {filteredRuns.length === 0 ? (
                    <div className="px-4 py-10 text-center text-sm text-slate-500">
                        No matching model runs
                    </div>
                ) : (
                    <div>
                        {filteredRuns.map((run) => {
                            const runId = String(run.id);
                            const isActive = selectedId === runId;
                            const isVisible = activeCompareIds.includes(runId);

                            return (
                                <div
                                    key={run.id}
                                    className={cn(
                                        "flex items-center gap-3 border-b border-slate-100 px-4 py-3 last:border-b-0",
                                        isActive ? "bg-slate-100" : "hover:bg-slate-50",
                                        !isVisible && "text-slate-400"
                                    )}
                                >
                                    <button
                                        type="button"
                                        onClick={() => toggleVisibility(runId)}
                                        className={cn(
                                            "inline-flex size-7 items-center justify-center rounded-md border transition",
                                            isVisible
                                                ? "border-slate-300 text-slate-700 hover:bg-slate-100"
                                                : "border-slate-200 text-slate-400 hover:bg-slate-50"
                                        )}
                                        aria-label={`${isVisible ? "Hide" : "Show"} ${run.run || `run-${run.id}`}`}
                                        title={isVisible ? "Visible" : "Hidden"}
                                    >
                                        <EyeIcon visible={isVisible} />
                                    </button>
                                    <button
                                        onClick={() => onSelect(run)}
                                        className={cn(
                                            "min-w-0 flex-1 text-left text-sm transition",
                                            isActive ? "font-medium text-slate-900" : "text-slate-700",
                                            !isVisible && "text-slate-400"
                                        )}
                                    >
                                        <span className="block truncate">{run.run || `run-${run.id}`}</span>
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => onDelete(run)}
                                        className="inline-flex size-7 items-center justify-center rounded-md border border-slate-200 text-slate-400 transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600"
                                        aria-label={`Delete ${run.run || `run-${run.id}`}`}
                                        title="Delete run"
                                    >
                                        <TrashIcon />
                                    </button>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>
        </aside>
    );
}
