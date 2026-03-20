"use client";

import { useState } from "react";

interface Props {
    open: boolean;
    onClose: () => void;
    onCreate: (data: { name: string; description?: string }) => void;
}

export default function CreateProjectModal({
    open,
    onClose,
    onCreate,
}: Props) {
    const [name, setName] = useState("");
    const [description, setDescription] = useState("");
    const [loading, setLoading] = useState(false);

    if (!open) return null;

    const handleSubmit = async () => {
        if (!name.trim()) return;

        try {
            setLoading(true);

            await onCreate({
                name: name.trim(),
                description: description.trim(),
            });

            // reset
            setName("");
            setDescription("");

            onClose();
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
            <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-[400px]">

                <h2 className="text-lg font-semibold mb-4">
                    Create New Project
                </h2>

                {/* Name */}
                <div className="mb-3">
                    <label className="text-sm text-gray-400">
                        Project Name
                    </label>
                    <input
                        className="w-full mt-1 px-3 py-2 rounded bg-gray-800 border border-gray-700 text-sm"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="Enter project name"
                    />
                </div>

                {/* Description */}
                <div className="mb-4">
                    <label className="text-sm text-gray-400">
                        Description
                    </label>
                    <textarea
                        className="w-full mt-1 px-3 py-2 rounded bg-gray-800 border border-gray-700 text-sm"
                        value={description}
                        onChange={(e) =>
                            setDescription(e.target.value)
                        }
                        placeholder="Optional description"
                    />
                </div>

                {/* Actions */}
                <div className="flex justify-end gap-2">
                    <button
                        onClick={onClose}
                        className="px-3 py-1 text-sm text-gray-400 hover:text-white"
                    >
                        Cancel
                    </button>

                    <button
                        onClick={handleSubmit}
                        disabled={loading}
                        className="px-4 py-1 text-sm bg-blue-600 hover:bg-blue-700 rounded"
                    >
                        {loading ? "Creating..." : "Create"}
                    </button>
                </div>

            </div>
        </div>
    );
}