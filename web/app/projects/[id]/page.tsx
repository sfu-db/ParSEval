"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import ModelRunComparision from "@/components/projects/ModelRunComparision";
import ModelRunSidebar from "@/components/projects/ModelRunSidebar";
import { ModelRunAPI } from "@/lib/api/modelRun";
import { ProjectAPI } from "@/lib/api/project";
import { ModelRun, Project } from "@/lib/types";

export default function ProjectPage() {
    const router = useRouter();
    const params = useParams();
    const projectId = params?.id ? Number(params.id) : undefined;

    const [project, setProject] = useState<Project | null>(null);
    const [modelRuns, setModelRuns] = useState<ModelRun[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [compareRunIds, setCompareRunIds] = useState<string[]>([]);
    const [compareInitialized, setCompareInitialized] = useState(false);

    function handleSelect(modelRun: ModelRun) {
        if (typeof projectId !== "number") return;
        router.push(`/projects/${projectId}/modelrun/${modelRun.id}`);
    }

    async function handleDelete(modelRun: ModelRun) {
        const confirmed = window.confirm(`Delete ${modelRun.run || `run-${modelRun.id}`}?`);
        if (!confirmed || typeof projectId !== "number") {
            return;
        }

        try {
            await ModelRunAPI.delete(projectId, modelRun.id);
            setModelRuns((current) => current.filter((run) => run.id !== modelRun.id));
            setCompareRunIds((current) => current.filter((id) => id !== String(modelRun.id)));
        } catch (err) {
            console.error("Failed to delete model run:", err);
            window.alert("Failed to delete model run.");
        }
    }

    useEffect(() => {
        async function load() {
            if (typeof projectId !== "number" || Number.isNaN(projectId)) {
                setError("Invalid project");
                setLoading(false);
                return;
            }

            try {
                setLoading(true);
                setError(null);

                const [proj, runs] = await Promise.all([
                    ProjectAPI.getById(projectId),
                    ModelRunAPI.getAll(projectId),
                ]);

                if (!proj) {
                    throw new Error("Project not found");
                }

                setProject(proj);
                setModelRuns(runs);
            } catch (err) {
                console.error("Failed to load project:", err);
                setError("Failed to load project data");
            } finally {
                setLoading(false);
            }
        }

        load();
    }, [projectId]);

    const latestRuns = useMemo(() => {
        return [...modelRuns].sort((a, b) => {
            const aTime = new Date(a.createdAt).getTime();
            const bTime = new Date(b.createdAt).getTime();
            return bTime - aTime;
        });
    }, [modelRuns]);

    useEffect(() => {
        const allRunIds = latestRuns.map((run) => String(run.id));

        if (!compareInitialized) {
            setCompareRunIds(allRunIds);
            setCompareInitialized(true);
            return;
        }

        setCompareRunIds((prev) => {
            const prevSet = new Set(prev);
            const kept = allRunIds.filter((id) => prevSet.has(id));
            const added = allRunIds.filter((id) => !prevSet.has(id));
            return [...kept, ...added];
        });
    }, [latestRuns, compareInitialized]);

    if (loading) {
        return <div className="p-6 text-muted-foreground">Loading...</div>;
    }

    if (error) {
        return <div className="p-6 text-destructive">{error}</div>;
    }

    if (!project) {
        return <div className="p-6 text-destructive">Project not found</div>;
    }

    return (
        <div className="flex h-full min-h-screen bg-background text-foreground">
            <ModelRunSidebar
                modelRuns={latestRuns}
                selectedId={null}
                onSelect={handleSelect}
                onUpload={() => {
                    router.push(`/projects/${project.id}/upload`);
                }}
                onDelete={handleDelete}
                compareIds={compareRunIds}
                onCompareChange={setCompareRunIds}
            />

            <main className="min-w-0 flex-1 overflow-y-auto p-6 lg:p-8">
                <div className="mx-auto flex max-w-7xl flex-col gap-6">
                    <ModelRunComparision
                        projectName={project.name}
                        modelRuns={latestRuns}
                        compareIds={compareRunIds}
                        onRunSelect={handleSelect}
                    />
                </div>
            </main>
        </div>
    );
}
