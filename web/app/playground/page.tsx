import Panel from "@/components/layout/Panel";

export default function Playground() {
    return (
        <div className="min-h-screen bg-gray-100 flex flex-col items-center justify-center p-8">
            <div className="w-full max-w-3xl grid grid-cols-1 gap-6">
                <div className="p-6 bg-white rounded-xl shadow-md">
                    <Panel>
                        <h3 className="text-xl font-semibold mb-4 text-blue-700">SQL Query 1</h3>
                        <textarea
                            className="w-full h-32 p-3 mb-6 rounded-lg border border-gray-300 focus:border-blue-400 focus:ring-2 focus:ring-blue-100 text-sm bg-gray-50 resize-none"
                            placeholder="Enter first SQL query..."
                        />
                        <h3 className="text-xl font-semibold mb-4 text-blue-700">SQL Query 2</h3>
                        <textarea
                            className="w-full h-32 p-3 rounded-lg border border-gray-300 focus:border-blue-400 focus:ring-2 focus:ring-blue-100 text-sm bg-gray-50 resize-none"
                            placeholder="Enter second SQL query..."
                        />
                        <div className="flex justify-end mt-6">
                            <button className="px-6 py-2 bg-blue-600 text-white rounded-lg font-medium shadow hover:bg-blue-700 transition">Run Equivalence Check</button>
                        </div>
                    </Panel>
                </div>
                <div className="p-6 bg-blue-50 rounded-xl shadow-md">
                    <Panel>
                        <h3 className="text-xl font-semibold mb-4 text-blue-700">Result</h3>
                        <p className="text-gray-600 text-base">Run equivalence check to see results</p>
                    </Panel>
                </div>
            </div>
        </div>
    );
}