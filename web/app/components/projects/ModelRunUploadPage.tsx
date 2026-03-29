"use client";

import type { ChangeEvent, DragEvent } from "react";
import { useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    ModelRunAPI,
    type ModelRunUploadPayload,
    type UploadEvalRecord,
} from "@/lib/api/modelRun";
import { getSettingsForRows } from "@/lib/settings";

type UploadRecord = UploadEvalRecord;

type ParsedUpload = {
    run?: Partial<ModelRunUploadPayload["run"]>;
    results: UploadRecord[];
};

function normalizeParsedRecord(
    value: unknown,
    fallbackDataset: string
): UploadRecord | null {
    if (!value || typeof value !== "object") return null;

    const record = value as Record<string, unknown>;
    if (
        typeof record.question_id !== "number" ||
        typeof record.db_id !== "string" ||
        typeof record.gold !== "string" ||
        typeof record.pred !== "string"
    ) {
        return null;
    }

    return {
        question_id: record.question_id,
        db_id: record.db_id,
        dataset:
            typeof record.dataset === "string" && record.dataset
                ? record.dataset
                : fallbackDataset,
        schema: record.schema,
        host_or_path:
            typeof record.host_or_path === "string"
                ? record.host_or_path
                : undefined,
        question: typeof record.question === "string" ? record.question : "",
        evidence:
            typeof record.evidence === "string" ? record.evidence : undefined,
        gold: typeof record.gold === "string" ? record.gold : "",
        prompt: typeof record.prompt === "string" ? record.prompt : "",
        pred: typeof record.pred === "string" ? record.pred : "",
        labels:
            record.labels && typeof record.labels === "object"
                ? (record.labels as UploadRecord["labels"])
                : undefined,
    };
}

function parseUploadText(text: string, fallbackDataset: string): ParsedUpload {
    const trimmed = text.trim();
    if (!trimmed) {
        throw new Error("The uploaded file is empty.");
    }

    const parseAsResults = (items: unknown[]) => {
        const invalidRows: number[] = [];
        const results = items
            .map((item, index) => {
                const normalized = normalizeParsedRecord(item, fallbackDataset);
                if (!normalized) invalidRows.push(index + 1);
                return normalized;
            })
            .filter((item): item is UploadRecord => item !== null);

        if (!results.length) {
            throw new Error("No valid result records were found in the file.");
        }
        if (invalidRows.length) {
            const preview = invalidRows.slice(0, 5).join(", ");
            throw new Error(
                `Some rows are invalid or missing required fields (question_id, db_id, gold, pred). Invalid rows: ${preview}${
                    invalidRows.length > 5 ? ", ..." : ""
                }.`
            );
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
                    run:
                        object.run && typeof object.run === "object"
                            ? (object.run as Partial<ModelRunUploadPayload["run"]>)
                            : undefined,
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
    const datasets = Array.from(
        new Set(results.map((result) => result.dataset).filter(Boolean))
    );
    return datasets.length === 1 ? datasets[0] : "";
}

function buildUploadPayload(
    parsedUpload: ParsedUpload,
    runOverrides: ModelRunUploadPayload["run"]
): ModelRunUploadPayload {
    return {
        run: {
            ...parsedUpload.run,
            ...runOverrides,
        },
        results: parsedUpload.results.map((result) => ({
            ...result,
            dataset: result.dataset || runOverrides.dataset,
        })),
    };
}

export default function ModelRunUploadPage() {
    const params = useParams();
    const router = useRouter();
    const projectId =
        params?.id && !Array.isArray(params.id) ? Number(params.id) : undefined;

    const [runName, setRunName] = useState("");
    const [modelName, setModelName] = useState("");
    const [datasetName, setDatasetName] = useState("");
    const [promptTemplate, setPromptTemplate] = useState("");
    const [status, setStatus] = useState("pending");
    const [fileName, setFileName] = useState("");
    const [isDragging, setIsDragging] = useState(false);
    const [parsedUpload, setParsedUpload] = useState<ParsedUpload | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [submitting, setSubmitting] = useState(false);
    const fileInputRef = useRef<HTMLInputElement | null>(null);

    const previewSettings = useMemo(
        () =>
            getSettingsForRows(
                (parsedUpload?.results ?? []).map((result) => ({
                    ...result,
                    labels: result.labels ?? {},
                }))
            ),
        [parsedUpload]
    );
    const previewDataset = useMemo(
        () => inferDataset(parsedUpload?.results ?? []),
        [parsedUpload]
    );

    function applyParsedUpload(parsed: ParsedUpload, sourceLabel: string) {
        const inferredDataset = parsed.run?.dataset || inferDataset(parsed.results);

        setParsedUpload(parsed);
        setFileName(sourceLabel);
        setError(null);

        if (!modelName.trim() && parsed.run?.model) setModelName(parsed.run.model);
        if (!runName.trim() && parsed.run?.run) setRunName(parsed.run.run);
        if (!promptTemplate.trim() && parsed.run?.promptTemplate) {
            setPromptTemplate(parsed.run.promptTemplate);
        }
        if (!datasetName.trim() && inferredDataset) setDatasetName(inferredDataset);
        if (parsed.run?.status) setStatus(parsed.run.status);
    }

    async function processFile(file: File) {
        try {
            const text = await file.text();
            const parsed = parseUploadText(text, datasetName.trim());
            applyParsedUpload(parsed, file.name);
        } catch (err) {
            console.error(err);
            setParsedUpload(null);
            setFileName("");
            setError(
                err instanceof Error
                    ? err.message
                    : "Failed to parse upload file."
            );
        }
    }

    async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
        const file = event.target.files?.[0];
        if (!file) return;
        await processFile(file);
        event.target.value = "";
    }

    function handleDragOver(event: DragEvent<HTMLDivElement>) {
        event.preventDefault();
        setIsDragging(true);
    }

    function handleDragLeave(event: DragEvent<HTMLDivElement>) {
        event.preventDefault();
        setIsDragging(false);
    }

    async function handleDrop(event: DragEvent<HTMLDivElement>) {
        event.preventDefault();
        setIsDragging(false);
        const file = event.dataTransfer.files?.[0];
        if (!file) return;
        await processFile(file);
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

        const payload = buildUploadPayload(parsedUpload, {
            model: modelName.trim(),
            dataset: resolvedDataset,
            run: runName.trim() || undefined,
            promptTemplate: promptTemplate.trim() || undefined,
            status,
            ...(parsedUpload.run?.setting
                ? { setting: parsedUpload.run.setting }
                : {}),
        });

        try {
            setSubmitting(true);
            setError(null);
            const createdRun = await ModelRunAPI.uploadResults(projectId, payload);
            router.push(`/projects/${projectId}/modelrun/${createdRun.id}`);
        } catch (err) {
            console.error(err);
            setError(
                err instanceof Error
                    ? err.message
                    : "Failed to upload model run results."
            );
        } finally {
            setSubmitting(false);
        }
    }

    return (
        <div className="mx-auto flex max-w-5xl flex-col gap-6 p-6 lg:p-8">
            <Card className="border-border shadow-sm">
                <CardHeader>
                    <CardTitle>Upload Model Run Results</CardTitle>
                </CardHeader>
                <CardContent className="grid gap-6">
                    <div className="grid gap-4 md:grid-cols-2">
                        <label className="grid gap-2 text-sm text-foreground/88">
                            <span className="font-medium">Run Name</span>
                            <input value={runName} onChange={(event) => setRunName(event.target.value)} placeholder="run_014" className="rounded-lg border border-border bg-background px-3 py-2 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />
                        </label>
                        <label className="grid gap-2 text-sm text-foreground/88">
                            <span className="font-medium">Model Name</span>
                            <input value={modelName} onChange={(event) => setModelName(event.target.value)} placeholder="gpt-4o-mini" className="rounded-lg border border-border bg-background px-3 py-2 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />
                        </label>
                        <label className="grid gap-2 text-sm text-foreground/88">
                            <span className="font-medium">Dataset</span>
                            <input value={datasetName} onChange={(event) => setDatasetName(event.target.value)} placeholder="spider" className="rounded-lg border border-border bg-background px-3 py-2 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />
                        </label>
                        <label className="grid gap-2 text-sm text-foreground/88">
                            <span className="font-medium">Status</span>
                            <select value={status} onChange={(event) => setStatus(event.target.value)} className="rounded-lg border border-border bg-background px-3 py-2 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/10">
                                <option value="pending">Pending</option>
                                <option value="running">Running</option>
                                <option value="completed">Completed</option>
                                <option value="failed">Failed</option>
                            </select>
                        </label>
                    </div>

                    <label className="grid gap-2 text-sm text-foreground/88">
                        <span className="font-medium">Prompt Template</span>
                        <input value={promptTemplate} onChange={(event) => setPromptTemplate(event.target.value)} placeholder="standard_cot" className="rounded-lg border border-border bg-background px-3 py-2 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />
                    </label>

                    <div
                        className={"grid gap-3 rounded-xl border border-dashed p-5 transition-colors " + (isDragging ? "border-primary bg-accent/70" : "border-border bg-muted/35")}
                        onDragOver={handleDragOver}
                        onDragLeave={handleDragLeave}
                        onDrop={handleDrop}
                    >
                        <div>
                            <div className="text-sm font-medium text-foreground">Result File</div>
                            <div className="mt-1 text-sm text-muted-foreground">Upload or drag a raw Text-to-SQL result JSON or JSONL file here. Each row should include `dataset`, `schema`, `question`, `db_id`, `question_id`, `evidence`, `gold`, `pred`, and `prompt`.</div>
                        </div>
                        <input ref={fileInputRef} type="file" accept=".json,.jsonl,application/json" onChange={handleFileChange} className="hidden" />
                        <button
                            type="button"
                            onClick={() => fileInputRef.current?.click()}
                            className={"rounded-xl border border-dashed px-4 py-8 text-left transition-colors " + (isDragging ? "border-primary bg-card" : "border-border bg-card hover:border-primary/60 hover:bg-accent/35")}
                        >
                            <div className="text-sm font-medium text-foreground">Drop file here or click to browse</div>
                            <div className="mt-1 text-sm text-muted-foreground">The backend will evaluate these raw predictions after upload.</div>
                        </button>
                        {fileName ? <div className="text-sm text-foreground/80">Loaded file: <span className="font-medium text-foreground">{fileName}</span></div> : null}
                    </div>

                    {error ? <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div> : null}

                    <div className="grid gap-4 md:grid-cols-3">
                        <div className="rounded-xl border border-border bg-background/65 p-4">
                            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Parsed Rows</div>
                            <div className="mt-2 text-3xl font-semibold text-foreground">{parsedUpload?.results.length ?? 0}</div>
                        </div>
                        <div className="rounded-xl border border-border bg-background/65 p-4">
                            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Detected Dataset</div>
                            <div className="mt-2 text-xl font-semibold text-foreground">{previewDataset || "Mixed / Missing"}</div>
                        </div>
                        <div className="rounded-xl border border-border bg-background/65 p-4">
                            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Detected Settings</div>
                            <div className="mt-2 text-xl font-semibold text-foreground">{previewSettings.length}</div>
                        </div>
                    </div>

                    {parsedUpload?.results.length ? (
                        <div className="overflow-hidden rounded-xl border border-border">
                            <div className="border-b border-border bg-muted/45 px-4 py-3 text-sm font-medium text-foreground">Preview</div>
                            <div className="overflow-x-auto">
                                <table className="min-w-full text-sm">
                                    <thead className="bg-muted/45 text-left text-xs uppercase tracking-[0.16em] text-muted-foreground">
                                        <tr>
                                            <th className="px-4 py-3 font-medium">Q #</th>
                                            <th className="px-4 py-3 font-medium">DB</th>
                                            <th className="px-4 py-3 font-medium">Question</th>
                                            <th className="px-4 py-3 font-medium">Pred SQL</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {parsedUpload.results.slice(0, 5).map((result) => (
                                            <tr key={`${result.db_id}-${result.question_id}`} className="border-t border-border/65 align-top">
                                                <td className="px-4 py-3 font-mono text-muted-foreground">Q{result.question_id}</td>
                                                <td className="px-4 py-3 font-mono text-muted-foreground">{result.db_id}</td>
                                                <td className="px-4 py-3 text-foreground/88">{result.question || "-"}</td>
                                                <td className="px-4 py-3 font-mono text-xs text-foreground/80">{result.pred || "-"}</td>
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
