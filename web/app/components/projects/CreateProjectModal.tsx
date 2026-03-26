"use client";

import { useState } from "react";

import {
    DB_LEVEL_OPTIONS,
    DEFAULT_PROJECT_SETTINGS,
    PROJECT_SETTINGS_EXPLANATION,
    QUERY_LEVEL_OPTIONS,
    type DBLevel,
    type ProjectSettings,
    type QueryLevel,
} from "@/lib/types";

interface Props {
    open: boolean;
    onClose: () => void;
    onCreate: (data: { name: string; description?: string; settings: ProjectSettings }) => void;
}

function toggleChoice<T extends string>(items: T[], value: T) {
    return items.includes(value) ? items.filter((item) => item !== value) : [...items, value];
}

function HelpBadge({ text }: { text: string }) {
    return (
        <span className="group relative inline-flex">
            <span className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-gray-500 text-[10px] font-bold text-gray-300 cursor-help">
                ?
            </span>
            <span className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 hidden w-64 -translate-x-1/2 rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-xs font-normal leading-5 text-gray-100 shadow-lg group-hover:block">
                {text}
            </span>
        </span>
    );
}

export default function CreateProjectModal({
    open,
    onClose,
    onCreate,
}: Props) {
    const [name, setName] = useState("");
    const [description, setDescription] = useState("");
    const [settings, setSettings] = useState<ProjectSettings>(DEFAULT_PROJECT_SETTINGS);
    const [loading, setLoading] = useState(false);

    if (!open) return null;

    const updateNumber = (key: keyof ProjectSettings, value: string) => {
        const parsed = Number(value);
        setSettings((current) => ({
            ...current,
            [key]: Number.isFinite(parsed) ? parsed : 0,
        }));
    };

    const handleSubmit = async () => {
        if (!name.trim()) return;
        if (!settings.dbLevels.length || !settings.queryLevels.length) return;

        try {
            setLoading(true);

            await onCreate({
                name: name.trim(),
                description: description.trim(),
                settings,
            });

            setName("");
            setDescription("");
            setSettings(DEFAULT_PROJECT_SETTINGS);
            onClose();
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
            <div className="max-h-[90vh] w-[720px] overflow-y-auto rounded-xl border border-gray-700 bg-gray-900 p-6">
                <h2 className="mb-4 text-lg font-semibold">Create New Project</h2>

                <div className="mb-3">
                    <label className="text-sm text-gray-400">Project Name</label>
                    <input
                        className="mt-1 w-full rounded bg-gray-800 px-3 py-2 text-sm border border-gray-700"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="Enter project name"
                    />
                </div>

                <div className="mb-4">
                    <label className="text-sm text-gray-400">Description</label>
                    <textarea
                        className="mt-1 w-full rounded bg-gray-800 px-3 py-2 text-sm border border-gray-700"
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        placeholder="Optional description"
                    />
                </div>

                <div className="mb-4 grid gap-4 md:grid-cols-2">
                    <div>
                        <label className="mb-2 flex items-center gap-2 text-sm text-gray-400">DB Levels <HelpBadge text={PROJECT_SETTINGS_EXPLANATION.dbLevels} /></label>
                        <div className="grid grid-cols-2 gap-2 rounded-lg border border-gray-700 bg-gray-800 p-3">
                            {DB_LEVEL_OPTIONS.map((level) => (
                                <label key={level} className="flex items-center gap-2 text-sm text-gray-200">
                                    <input
                                        type="checkbox"
                                        checked={settings.dbLevels.includes(level)}
                                        onChange={() =>
                                            setSettings((current) => ({
                                                ...current,
                                                dbLevels: toggleChoice(current.dbLevels, level as DBLevel),
                                            }))
                                        }
                                    />
                                    <span>{level}</span>
                                </label>
                            ))}
                        </div>
                    </div>

                    <div>
                        <label className="mb-2 flex items-center gap-2 text-sm text-gray-400">Query Levels <HelpBadge text={PROJECT_SETTINGS_EXPLANATION.queryLevels} /></label>
                        <div className="grid grid-cols-2 gap-2 rounded-lg border border-gray-700 bg-gray-800 p-3">
                            {QUERY_LEVEL_OPTIONS.map((level) => (
                                <label key={level} className="flex items-center gap-2 text-sm text-gray-200">
                                    <input
                                        type="checkbox"
                                        checked={settings.queryLevels.includes(level)}
                                        onChange={() =>
                                            setSettings((current) => ({
                                                ...current,
                                                queryLevels: toggleChoice(current.queryLevels, level as QueryLevel),
                                            }))
                                        }
                                    />
                                    <span>{level}</span>
                                </label>
                            ))}
                        </div>
                    </div>
                </div>

                <div className="mb-4 rounded-lg border border-gray-700 bg-gray-800 p-4">
                    <label className="mb-3 flex items-center gap-2 text-sm text-gray-300">
                        <input
                            type="checkbox"
                            checked={settings.set_semantic}
                            onChange={(e) =>
                                setSettings((current) => ({
                                    ...current,
                                    set_semantic: e.target.checked,
                                }))
                            }
                        />
                        <span>Set semantic</span><HelpBadge text={PROJECT_SETTINGS_EXPLANATION.set_semantic} />
                    </label>

                    <div className="grid gap-3 md:grid-cols-2">
                        {[
                            "global_timeout",
                            "query_timeout",
                            "null_threshold",
                            "unique_threshold",
                            "duplicate_threshold",
                            "group_count_threshold",
                            "group_size_threshold",
                            "positive_threshold",
                            "negative_threshold",
                            "min_rows",
                            "max_tries",
                        ].map((key) => (
                            <label key={key} className="grid gap-1 text-sm text-gray-400">
                                <span className="flex items-center gap-2">{key}<HelpBadge text={PROJECT_SETTINGS_EXPLANATION[key as keyof ProjectSettings]} /></span>
                                <input
                                    type="number"
                                    className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100"
                                    value={String(settings[key as keyof ProjectSettings])}
                                    onChange={(event) => updateNumber(key as keyof ProjectSettings, event.target.value)}
                                />
                            </label>
                        ))}
                    </div>
                </div>

                <div className="flex justify-end gap-2">
                    <button
                        onClick={onClose}
                        className="px-3 py-1 text-sm text-gray-400 hover:text-white"
                    >
                        Cancel
                    </button>

                    <button
                        onClick={handleSubmit}
                        disabled={loading || !settings.dbLevels.length || !settings.queryLevels.length}
                        className="rounded bg-blue-600 px-4 py-1 text-sm hover:bg-blue-700 disabled:opacity-50"
                    >
                        {loading ? "Creating..." : "Create"}
                    </button>
                </div>
            </div>
        </div>
    );
}
