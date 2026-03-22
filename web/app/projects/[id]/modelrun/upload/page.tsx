"use client";

import type { ChangeEvent } from "react";
import { useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ModelRunAPI, type ModelRunUploadPayload } from "@/lib/api/modelRun";
import { getSettingsForRows } from "@/lib/settings";
import type { EvalRecord } from "@/lib/types";

type UploadRecord = Omit<EvalRecord, "runId">;

type ParsedUpload = {
    run?: Partial<ModelRunUploadPayload["run"]>;
    results: UploadRecord[];
};

function normalizeParsedRecord(value: unknown, fallbackDataset: string): UploadRecord | null {
    if (!value || typeof value !== "object") return null;

    const record = value as Record<string, unknown>;
    const labels = record.labels && typeof record.labels === "object" ? (record.labels as UploadRecord["labels"]) : {};

    if (typeof record.question_id !== "number" || typeof record.db_id !== "string") {
        return null;
    }

    return {
        question_id: record.question_id,
        db_id: record.db_id,
        dataset: typeof record.dataset === "string" && record.dataset ? record.dataset : fallbackDataset,
        host_or_path: typeof record.host_or_path === "string" ? record.host_or_path : "",
        question: typeof record.question === "string" ? record.question : "",
        evidence: typeof record.evidence === "string" ? record.evidence : undefined,
        gold: typeof record.gold === "string" ? record.gold : "",
        prompt: typeof record.prompt === "string" ? record.prompt : "",
        pred: typeof record.pred === "string" ? record.pred : "",
        labels,
    };
}

function parseUploadText(text: string, fallbackDataset: string): ParsedUpload {
    const trimmed = text.trim();
    if (!trimmed) {
        throw new Error("The uploaded file is empty.");
    }

    const parseAsResults = (items: unknown[]) => {
        const results = items
            .map((item) => normalizeParsedRecord(item, fallbackDataset))
            .filter((item): item is UploadRecord => item !== null);

        if (!results.length) {
            throw new Error("No valid result records were found in the file.");
        }

        return results;
    };

    if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
        const parsed = JSON.parse(trimmed) as unknown;

        if (Array.isArray(parsed)) {
            return { results: parseAsResults(parsed) };
        }

        if (parsed && typeof parsed === "object") {
            const object = parsed as Record<string, unknown>;
            if (Array.isArray(object.results)) {
                return {
                    run: object.run && typeof object.run === "object" ? (object.run as Partial<ModelRunUploadPayload["run"]>) : undefined,
                    results: parseAsResults(object.results),
                };
            }
        }
    }

    const lines = trimmed.split(/\r?\n/).filter(Boolean);
    const parsedLines = lines.map((line, index) => {
        try {
            return JSON.parse(line);
        } catch {
            throw new Error(`Invalid JSONL at line ${index + 1}.`);
        }
    });

    return { results: parseAsResults(parsedLines) };
}

function inferDataset(results: UploadRecord[]) {
    const datasets = Array.from(new Set(results.map((result) => result.dataset).filter(Boolean)));
    return datasets.length === 1 ? datasets[0] : "";
}

export default function ModelRunUploadPage() {
    const params = useParams();
    const router = useRouter();
    const projectId = params?.id && !Array.isArray(params.id) ? Number(params.id) : undefined;

    const [runName, setRunName] = useState("");
    const [modelName, setModelName] = useState("");
    const [datasetName, setDatasetName] = useState("");
    const [promptTemplate, setPromptTemplate] = useState("");
    const [status, setStatus] = useState("completed");
    const [fileName, setFileName] = useState("");
    const [parsedUpload, setParsedUpload] = useState<ParsedUpload | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [submitting, setSubmitting] = useState(false);

    const previewSettings = useMemo(() => getSettingsForRows(parsedUpload?.results ?? []), [parsedUpload]);
    const previewDataset = useMemo(() => inferDataset(parsedUpload?.results ?? []), [parsedUpload]);

    async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
        const file = event.target.files?.[0];
        if (!file) return;

        try {
            const text = await file.text();
            const parsed = parseUploadText(text, datasetName.trim());
            const inferredDataset = parsed.run?.dataset || inferDataset(parsed.results);

            setParsedUpload(parsed);
            setFileName(file.name);
            setError(null);

            if (!modelName.trim() && parsed.run?.model) setModelName(parsed.run.model);
            if (!runName.trim() && parsed.run?.run) setRunName(parsed.run.run);
            if (!promptTemplate.trim() && parsed.run?.promptTemplate) setPromptTemplate(parsed.run.promptTemplate);
            if (!datasetName.trim() && inferredDataset) setDatasetName(inferredDataset);
            if (parsed.run?.status) setStatus(parsed.run.status);
        } catch (err) {
            console.error(err);
            setParsedUpload(null);
            setFileName("");
            setError(err instanceof Error ? err.message : "Failed to parse upload file.");
        }
    }

    async function handleSubmit() {
        if (!projectId || Number.isNaN(projectId)) {
            setError("Invalid project id.");
            return;
        }
        if (!parsedUpload?.results.length) {
            setError("Upload a result file before creating the model run.");
            return;
        }
        if (!modelName.trim()) {
            setError("Model name is required.");
            return;
        }

        const resolvedDataset = datasetName.trim() || previewDataset;
        if (!resolvedDataset) {
            setError("Dataset name is required.");
            return;
        }

        const payload: ModelRunUploadPayload = {
            run: {
                model: modelName.trim(),
                dataset: resolvedDataset,
                run: runName.trim() || undefined,
                promptTemplate: promptTemplate.trim() || undefined,
                status,
                ...(parsedUpload.run?.metric ? { metric: parsedUpload.run.metric } : {}),
                ...(parsedUpload.run?.setting ? { setting: parsedUpload.run.setting } : {}),
            },
            results: parsedUpload.results.map((result) => ({
                ...result,
                dataset: result.dataset || resolvedDataset,
            })),
        };

        try {
            setSubmitting(true);
            setError(null);
            const createdRun = await ModelRunAPI.uploadResults(projectId, payload);
            router.push(`/projects/${projectId}/modelrun/${createdRun.id}`);
        } catch (err) {
            console.error(err);
            setError(err instanceof Error ? err.message : "Failed to upload model run results.");
        } finally {
            setSubmitting(false);
        }
    }

    return (
        <div className="mx-auto flex max-w-5xl flex-col gap-6 p-6 lg:p-8">
            <Card>
                <CardHeader>
                    <CardTitle>Upload Model Run Results</CardTitle>
                </CardHeader>
                <CardContent className="grid gap-6">
                    <div className="grid gap-4 md:grid-cols-2">
                        <label className="grid gap-2 text-sm text-slate-700">
                            <span className="font-medium">Run Name</span>
                            <input value={runName} onChange={(event) => setRunName(event.target.value)} placeholder="run_014" className="rounded-lg border border-slate-200 px-3 py-2 outline-none focus:border-slate-400" />
                        </label>
                        <label className="grid gap-2 text-sm text-slate-700">
                            <span className="font-medium">Model Name</span>
                            <input value={modelName} onChange={(event) => setModelName(event.target.value)} placeholder="gpt-4o-mini" className="rounded-lg border border-slate-200 px-3 py-2 outline-none focus:border-slate-400" />
                        </label>
                        <label className="grid gap-2 text-sm text-slate-700">
                            <span className="font-medium">Dataset</span>
                            <input value={datasetName} onChange={(event) => setDatasetName(event.target.value)} placeholder="spider" className="rounded-lg border border-slate-200 px-3 py-2 outline-none focus:border-slate-400" />
                        </label>
                        <label className="grid gap-2 text-sm text-slate-700">
                            <span className="font-medium">Status</span>
                            <select value={status} onChange={(event) => setStatus(event.target.value)} className="rounded-lg border border-slate-200 bg-white px-3 py-2 outline-none focus:border-slate-400">
                                <option value="completed">Completed</option>
                                <option value="running">Running</option>
                                <option value="pending">Pending</option>
                                <option value="failed">Failed</option>
                            </select>
                        </label>
                    </div>

                    <label className="grid gap-2 text-sm text-slate-700">
                        <span className="font-medium">Prompt Template</span>
                        <input value={promptTemplate} onChange={(event) => setPromptTemplate(event.target.value)} placeholder="standard_cot" className="rounded-lg border border-slate-200 px-3 py-2 outline-none focus:border-slate-400" />
                    </label>

                    <div className="grid gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-5">
                        <div>
                            <div className="text-sm font-medium text-slate-900">Result File</div>
                            <div className="mt-1 text-sm text-slate-500">Upload a JSON or JSONL result file. Supported shapes: an array of eval records, or an object with `run` and `results` fields.</div>
                        </div>
                        <input type="file" accept=".json,.jsonl,application/json" onChange={handleFileChange} className="block text-sm text-slate-600 file:mr-4 file:rounded-md file:border-0 file:bg-slate-900 file:px-3 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-slate-700" />
                        {fileName ? <div className="text-sm text-slate-600">Loaded file: <span className="font-medium text-slate-900">{fileName}</span></div> : null}
                    </div>

                    {error ? <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div> : null}

                    <div className="grid gap-4 md:grid-cols-3">
                        <div className="rounded-xl border border-slate-200 p-4">
                            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Parsed Rows</div>
                            <div className="mt-2 text-3xl font-semibold text-slate-900">{parsedUpload?.results.length ?? 0}</div>
                        </div>
                        <div className="rounded-xl border border-slate-200 p-4">
                            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Detected Dataset</div>
                            <div className="mt-2 text-xl font-semibold text-slate-900">{previewDataset || "Mixed / Missing"}</div>
                        </div>
                        <div className="rounded-xl border border-slate-200 p-4">
                            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Detected Settings</div>
                            <div className="mt-2 text-xl font-semibold text-slate-900">{previewSettings.length}</div>
                        </div>
                    </div>

                    {parsedUpload?.results.length ? (
                        <div className="overflow-hidden rounded-xl border border-slate-200">
                            <div className="border-b border-slate-200 bg-slate-50 px-4 py-3 text-sm font-medium text-slate-900">Preview</div>
                            <div className="overflow-x-auto">
                                <table className="min-w-full text-sm">
                                    <thead className="bg-slate-50 text-left text-xs uppercase tracking-[0.16em] text-slate-500">
                                        <tr>
                                            <th className="px-4 py-3 font-medium">Q #</th>
                                            <th className="px-4 py-3 font-medium">DB</th>
                                            <th className="px-4 py-3 font-medium">Question</th>
                                            <th className="px-4 py-3 font-medium">Pred SQL</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {parsedUpload.results.slice(0, 5).map((result) => (
                                            <tr key={`${result.db_id}-${result.question_id}`} className="border-t border-slate-100 align-top">
                                                <td className="px-4 py-3 font-mono text-slate-500">Q{result.question_id}</td>
                                                <td className="px-4 py-3 font-mono text-slate-500">{result.db_id}</td>
                                                <td className="px-4 py-3 text-slate-700">{result.question || "-"}</td>
                                                <td className="px-4 py-3 font-mono text-xs text-slate-600">{result.pred || "-"}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    ) : null}

                    <div className="flex items-center justify-end gap-3">
                        <Button type="button" variant="outline" onClick={() => router.push(typeof projectId === "number" ? `/projects/${projectId}` : "/projects")}>Cancel</Button>
                        <Button type="button" onClick={handleSubmit} disabled={submitting || !parsedUpload?.results.length}>
                            {submitting ? "Uploading..." : "Create Model Run"}
                        </Button>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
