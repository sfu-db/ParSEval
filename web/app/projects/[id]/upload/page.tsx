"use client";

import { useState } from "react";
import UploadBox from "@/components/projects/UploadBox";
import ResultTable from "@/components/projects/ResultTable";

export default function UploadPage() {
    const [data, setData] = useState<any[]>([]);
    const [results, setResults] = useState<any[]>([]);

    const handleUpload = (jsonData: any[]) => {
        setData(jsonData);
    };

    const runParseval = async () => {
        const res = await fetch("http://localhost:8000/evaluate", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ data }),
        });

        const result = await res.json();
        setResults(result);
    };

    return (
        <div style={{ padding: 20 }}>
            <h2>Upload Dataset</h2>

            <UploadBox onUpload={handleUpload} />

            <button onClick={runParseval}>
                Run ParSEval
            </button>

            <ResultTable results={results} />
        </div>
    );
}