"use client";

export default function UploadBox({ onUpload }: any) {
    const handleFile = async (e: any) => {
        const file = e.target.files[0];
        const text = await file.text();
        const json = JSON.parse(text);
        onUpload(json);
    };

    return (
        <div>
            <input type="file" accept=".json" onChange={handleFile} />
        </div>
    );
}