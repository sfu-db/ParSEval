import { Card, CardContent } from "@/components/ui/card";

interface KPIProps {
    title: string;
    value: string | number;
}

export function KPICard({ title, value }: KPIProps) {
    return (
        <Card className="bg-card">
            <CardContent className="p-4">
                <div className="text-sm text-gray-400">{title}</div>
                <div className="text-2xl font-semibold mt-1">
                    {value}
                </div>
            </CardContent>
        </Card>
    );
}