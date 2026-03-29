import { useRef, useState, useEffect } from 'react';

export function ChartWrapper({
    children,
    height = 320,
}: {
    children: React.ReactNode;
    height?: number;
}) {
    const ref = useRef<HTMLDivElement>(null);
    const [visible, setVisible] = useState(false);

    useEffect(() => {
        const el = ref.current;
        if (!el) return;
        const obs = new ResizeObserver(([entry]) => {
            if (entry.contentRect.width > 0) setVisible(true);
        });
        obs.observe(el);
        return () => obs.disconnect();
    }, []);

    return (
        <div ref={ref} style={{ width: '100%', height }}>
            {visible && children}
        </div>
    );
}