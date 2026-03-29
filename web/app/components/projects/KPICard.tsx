import { Card, CardContent } from "@/components/ui/card";

interface KPIProps {
    title: string;
    value: string | number;
}

export function KPICard({ title, value }: KPIProps) {
    return (
        <Card className="border-border bg-card shadow-sm">
            <CardContent className="p-4">
                <div className="text-sm text-muted-foreground">{title}</div>
                <div className="mt-1 text-2xl font-semibold text-foreground">
                    {value}
                </div>
            </CardContent>
        </Card>
    );
}
