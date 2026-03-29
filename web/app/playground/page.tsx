import Panel from "@/components/layout/Panel";

export default function Playground() {
    return (
        <div className="flex min-h-screen flex-col items-center justify-center bg-gray-100 p-8 text-foreground">
            <div className="w-full max-w-3xl grid grid-cols-1 gap-6">
                <div className="p-6 bg-white rounded-xl shadow-md">
                    <Panel>
                        <h3 className="mb-4 text-xl font-semibold text-foreground">SQL Query 1</h3>
                        <textarea
                            className="mb-6 h-32 w-full resize-none rounded-lg border border-gray-300 bg-gray-50 p-3 text-sm text-foreground focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
                            placeholder="Enter first SQL query..."
                        />
                        <h3 className="mb-4 text-xl font-semibold text-foreground">SQL Query 2</h3>
                        <textarea
                            className="h-32 w-full resize-none rounded-lg border border-gray-300 bg-gray-50 p-3 text-sm text-foreground focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
                            placeholder="Enter second SQL query..."
                        />
                        <div className="mt-6 flex justify-end">
                            <button className="px-6 py-2 bg-blue-600 text-white rounded-lg font-medium shadow hover:bg-blue-700 transition">Run Equivalence Check</button>
                        </div>
                    </Panel>
                </div>
                <div className="p-6 bg-blue-50 rounded-xl shadow-md">
                    <Panel>
                        <h3 className="mb-4 text-xl font-semibold text-foreground">Result</h3>
                        <p className="text-base text-foreground">Run equivalence check to see results</p>
                    </Panel>
                </div>
            </div>
        </div>
    );
}
