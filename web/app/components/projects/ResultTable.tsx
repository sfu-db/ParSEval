export default function ResultTable({ results }: any) {
    return (
        <table border={1}>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Constraint</th>
                    <th>Result</th>
                </tr>
            </thead>
            <tbody>
                {results.map((r: any) => (
                    <tr key={r.id}>
                        <td>{r.id}</td>
                        <td>{r.setting}</td>
                        <td>{r.result}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}