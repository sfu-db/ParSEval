"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import Link from "next/link";

import { getProjectById, getExperimentsByProjectId } from "@/lib/api/project";
import { ExperimentResult } from "@/lib/types";
import ExperimentTrends from "@/components/projects/ExperimentGap";
import ExperimentList from "@/components/projects/ExperimentList";
import { useRouter } from "next/navigation";


export default function ProjectPage() {
    const router = useRouter();


    function handleSelect(exp: ExperimentResult) {
        router.push(
            `/projects/${exp.projectId}/experiments/${exp.id}`
        );
    }

    const params = useParams();
    // Next.js dynamic route params are always string or string[]
    const projectId = params?.id ? Number(params.id) : undefined;

    const [project, setProject] = useState<any>(null);
    const [experiments, setExperiments] = useState<any[]>([]);
    const [selectedExp, setSelectedExp] = useState<ExperimentResult | null>(null);

    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        async function load() {
            if (typeof projectId !== 'number' || isNaN(projectId)) return;
            try {
                setLoading(true);
                setError(null);

                const proj = await getProjectById(projectId as number);
                setProject(proj);

                const exps = await getExperimentsByProjectId(projectId as number);
                setExperiments(exps);

            } catch (err: any) {
                console.error("Failed to load project:", err);
                setError("Failed to load project data");
            } finally {
                setLoading(false);
            }
        }

        load();
    }, [projectId]);

    if (loading) {
        return <div className="p-6 text-gray-400">Loading...</div>;
    }

    if (error) {
        return <div className="p-6 text-red-400">{error}</div>;
    }

    if (!project) {
        return <div className="p-6 text-red-400">Project not found</div>;
    }

    return (
        <div className="h-full flex">
            <ExperimentList experiments={experiments}
                selectedId={selectedExp?.id ? String(selectedExp.id) : null}
                onSelect={handleSelect}
                onUpload={() => {
                    console.log("Upload prediction");
                    // TODO: open modal
                }}
            />

            <div className="flex-1 p-6">
                <ExperimentTrends experiments={experiments} />
            </div>



        </div>
    );
}