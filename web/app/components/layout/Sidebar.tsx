"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ProjectAPI } from "@/lib/api/project";
import { Project } from "@/lib/types";

import CreateProjectModal from "@/components/projects/CreateProjectModal";
import {
    Collapsible,
    CollapsibleTrigger,
    CollapsibleContent,
} from "@/components/ui/collapsible";

const Sidebar = () => {
    const [open, setOpen] = useState(false);
    const pathname = usePathname();

    const [projects, setProjects] = useState<Project[]>([]);
    const [projectListOpen, setProjectListOpen] = useState(true);

    useEffect(() => {
        const fetchProjects = async () => {
            try {
                const data = await ProjectAPI.getAll();
                setProjects(data);

            } catch (err) {
                console.error("Failed to load projects:", err);
            } finally {
            }
        };

        fetchProjects();
    }, []);

    const navItems = [
        {
            name: "Datasets",
            path: "/datasets",
            icon: (
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                        d="M19.428 15.428a2 2 0 00-1.022-.547l-2.384-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
                </svg>
            ),
        },
        {
            name: "Playground",
            path: "/playground",
            icon: (
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                        d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                </svg>
            ),
        },
    ];

    return (
        <aside className="w-64 bg-gray-900 text-white flex flex-col">
            <nav className="flex-1 p-4">
                <ul className="space-y-2">
                    <li>
                        <Collapsible open={projectListOpen} onOpenChange={setProjectListOpen}>

                            <CollapsibleTrigger className="flex items-center w-full px-4 py-3 rounded-lg hover:bg-gray-800">
                                <span className="flex-shrink-0">
                                    <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                                            d="M19.428 15.428a2 2 0 00-1.022-.547l-2.384-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
                                    </svg>
                                </span>
                                <span className="ml-3 flex-1 text-left">Projects</span>
                                <span
                                    className={`ml-auto transition-transform ${projectListOpen ? "rotate-90" : ""
                                        }`}
                                >
                                    <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                                            d="M9 5l7 7-7 7" />
                                    </svg>
                                </span>
                            </CollapsibleTrigger>

                            <CollapsibleContent>
                                <ul className="mt-2 space-y-1">
                                    {projects.map((proj) => {
                                        const isActive = pathname === `/projects/${proj.id}`;
                                        return (
                                            <li key={proj.id}>
                                                <Link
                                                    href={`/projects/${proj.id}`}
                                                    className={`block px-4 py-2 rounded-lg text-sm ${isActive
                                                        ? "bg-blue-600 text-white"
                                                        : "hover:bg-gray-800 text-gray-300"
                                                        }`}
                                                >
                                                    {proj.name}
                                                </Link>
                                            </li>
                                        );
                                    })}
                                    <li>
                                        <div className="border-t border-gray-700 my-2" />
                                    </li>
                                    <li>
                                        <button
                                            onClick={() => setOpen(true)}
                                            className="w-full flex items-center px-4 py-2 rounded-lg text-sm text-blue-400 hover:bg-gray-800"
                                        >
                                            <span className="mr-2 text-lg">+</span>
                                            New Project
                                        </button>
                                        <CreateProjectModal
                                            open={open}
                                            onClose={() => setOpen(false)}
                                            onCreate={async (data) => {
                                                const newProject = await ProjectAPI.create({ name: data.name, description: data.description, settings: data.settings });
                                                setProjects((prev) => [...prev, newProject]);
                                            }}
                                        />
                                    </li>

                                </ul>
                            </CollapsibleContent>
                        </Collapsible>
                    </li>

                    {/* ✅ Global Navigation */}
                    {navItems.map((item) => {
                        const isActive = pathname === item.path;

                        return (
                            <li key={item.path}>
                                <Link
                                    href={item.path}
                                    className={`flex items-center px-4 py-3 rounded-lg ${isActive ? "bg-blue-600" : "hover:bg-gray-800"
                                        }`}
                                >
                                    <span className="flex-shrink-0">{item.icon}</span>
                                    <span className="ml-3">{item.name}</span>
                                </Link>
                            </li>
                        );
                    })}

                </ul>
            </nav>

            <div className="p-4 border-t border-gray-700">
                <p className="text-sm text-gray-400">Version 0.0.1</p>
            </div>
        </aside>
    );
};


export default Sidebar;
