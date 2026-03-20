export default function Panel({ children }: { children: React.ReactNode }) {
    return (
        <div style={{
            flex: 1,
            padding: 16,
            overflow: "auto",
            borderRight: "1px solid #eee"
        }}>
            {children}
        </div>
    );
}