"use client";

import { ExperimentResult } from "@/lib/types";
import Panel from "@/components/layout/Panel";

interface Props {
    experiments: ExperimentResult[];
    selectedId: string | null;
    onSelect: (exp: ExperimentResult) => void;
    onUpload: () => void;
}

export default function ExperimentList({
    experiments,
    selectedId,
    onSelect,
    onUpload,
}: Props) {
    return (
        <div className="w-80 border-r border-gray-200 flex flex-col h-full bg-gradient-to-b from-blue-50 to-white">
            <Panel>
                {/* Upload Button */}
                <div className="p-6 border-b border-gray-200 flex justify-end items-center">
                    <button
                        onClick={onUpload}
                        className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-bold shadow-md flex items-center gap-2 transition"
                        title="Upload new experiment results"
                    >
                        <span className="text-lg">+</span>
                        <span>Upload</span>
                    </button>
                </div>

                {/* Experiment List */}
                <div className="flex-1 overflow-y-auto mt-2 space-y-2 px-2">
                    {experiments.length === 0 ? (
                        <p className="text-gray-400 text-sm p-2 italic">
                            No experiments yet
                        </p>
                    ) : (
                        experiments.map((exp) => {
                            const isActive = selectedId === String(exp.id);
                            return (
                                <button
                                    key={exp.id}
                                    onClick={() => onSelect(exp)}
                                    className={`w-full text-left px-4 py-3 rounded-lg text-base font-medium flex items-center gap-2 transition-all border border-transparent shadow-sm ${isActive
                                        ? "bg-blue-600 text-white border-blue-700 shadow-lg"
                                        : "bg-white text-gray-700 hover:bg-blue-100 hover:text-blue-700 hover:border-blue-300"
                                        }`}
                                    style={{ outline: isActive ? "2px solid #2563eb" : "none" }}
                                >
                                    <span className="inline-block w-2 h-2 rounded-full mr-2" style={{ background: isActive ? '#fff' : '#2563eb' }}></span>
                                    <span>{exp.name}</span>
                                    <span className="ml-auto text-xs text-gray-400">ID: {exp.id}</span>
                                </button>
                            );
                        })
                    )}
                </div>
            </Panel>
        </div>
    );

}