import { readFileSync } from "node:fs";

import type {
    DingTalkDirectoryStatusSummary,
    DingTalkDirectorySyncStatus,
} from "#admin/sources/oauth/DingTalkDirectoryPanel";

import { beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("#elements/tasks/ScheduleList", () => ({}));
vi.mock("#elements/forms/ConfirmationForm", () => ({}));

let dingtalkDirectoryStatusSummary: (
    statuses: DingTalkDirectorySyncStatus[],
) => DingTalkDirectoryStatusSummary;
let dingtalkDirectorySummaryMetrics: (statuses: DingTalkDirectorySyncStatus[]) => {
    key: string;
    value: number;
    label: string;
}[];
let DingTalkDirectoryPanelElement: new () => HTMLElement;
let directoryPanelSource = "";

function templateText(value: unknown): string {
    if (!value) {
        return "";
    }
    if (typeof value === "string" || typeof value === "number") {
        return String(value);
    }
    if (Array.isArray(value)) {
        return value.map((item) => templateText(item)).join("");
    }
    if (typeof value === "object" && "strings" in value && "values" in value) {
        const template = value as { strings: string[]; values: unknown[] };
        return template.strings.reduce(
            (text, string, index) => `${text}${string}${templateText(template.values[index])}`,
            "",
        );
    }
    return "";
}

describe("DingTalkDirectoryPanel", () => {
    beforeAll(async () => {
        directoryPanelSource = readFileSync(
            new URL("../../src/admin/sources/oauth/DingTalkDirectoryPanel.ts", import.meta.url),
            "utf8",
        );
        globalThis.CSSStyleSheet ??= class CSSStyleSheet {
            replaceSync(): void {
                return undefined;
            }
        } as unknown as typeof CSSStyleSheet;
        globalThis.HTMLElement ??= class HTMLElement {} as typeof HTMLElement;
        globalThis.HTMLIFrameElement ??= class HTMLIFrameElement {} as typeof HTMLIFrameElement;
        globalThis.customElements ??= {
            define: () => {},
            get: () => undefined,
            whenDefined: async () => undefined,
        } as unknown as CustomElementRegistry;
        const windowStub = {
            location: {
                origin: "http://localhost",
                search: "",
            },
            addEventListener: () => {},
            removeEventListener: () => {},
        } as unknown as Window & typeof globalThis;
        globalThis.window ??= windowStub;
        globalThis.self ??= windowStub;
        ({
            DingTalkDirectoryPanel: DingTalkDirectoryPanelElement,
            dingtalkDirectoryStatusSummary,
            dingtalkDirectorySummaryMetrics,
        } = await import("#admin/sources/oauth/DingTalkDirectoryPanel"));
    });

    it("counts successful and failed sync statuses", () => {
        const statuses: DingTalkDirectorySyncStatus[] = [
            {
                corpId: "corp-a",
                status: "success",
                startedAt: new Date("2026-06-11T01:00:00Z"),
                finishedAt: new Date("2026-06-11T01:02:00Z"),
                error: "",
                counters: { users: 10 },
            },
            {
                corpId: "corp-b",
                status: "error",
                startedAt: new Date("2026-06-11T02:00:00Z"),
                finishedAt: new Date("2026-06-11T02:01:00Z"),
                error: "missing permission",
                counters: {},
            },
            {
                corpId: "corp-c",
                status: "running",
                startedAt: new Date("2026-06-11T03:00:00Z"),
                finishedAt: null,
                error: "",
                counters: {},
            },
        ];

        expect(dingtalkDirectoryStatusSummary(statuses)).toEqual({
            total: 3,
            success: 1,
            error: 1,
            running: 1,
            unknown: 0,
        });
    });

    it("treats unrecognized or missing statuses as unknown", () => {
        expect(
            dingtalkDirectoryStatusSummary([
                {
                    corpId: "corp-a",
                    status: "queued",
                    startedAt: null,
                    finishedAt: null,
                    error: "",
                    counters: {},
                },
                {
                    corpId: "corp-b",
                    status: "",
                    startedAt: null,
                    finishedAt: null,
                    error: "",
                    counters: {},
                },
            ]),
        ).toEqual({
            total: 2,
            success: 0,
            error: 0,
            running: 0,
            unknown: 2,
        });
    });

    it("returns stable summary metrics with separate values and labels", () => {
        const metrics = dingtalkDirectorySummaryMetrics([
            {
                corpId: "corp-a",
                status: "success",
                startedAt: null,
                finishedAt: null,
                error: "",
                counters: {},
            },
            {
                corpId: "corp-b",
                status: "error",
                startedAt: null,
                finishedAt: null,
                error: "",
                counters: {},
            },
        ]);

        expect(metrics).toEqual([
            { key: "total", value: 2, label: "Corp sync records" },
            { key: "success", value: 1, label: "Successful" },
            { key: "error", value: 1, label: "Failed" },
            { key: "running", value: 0, label: "Running" },
        ]);
    });

    it("renders summary metrics with separate value and label elements", () => {
        const panel = new DingTalkDirectoryPanelElement();
        Object.assign(panel, {
            statuses: [
                {
                    corpId: "corp-a",
                    status: "success",
                    startedAt: null,
                    finishedAt: null,
                    error: "",
                    counters: {},
                },
            ],
        });

        const template = templateText(
            (
                panel as unknown as {
                    renderSummary(): unknown;
                }
            ).renderSummary(),
        );

        expect(template).toContain("ak-dingtalk-directory-summary-item");
        expect(template).toContain("ak-dingtalk-directory-summary-value");
        expect(template).toContain("ak-dingtalk-directory-summary-label");
    });

    it("renders a delete action for existing corp sync records", () => {
        expect(directoryPanelSource).toContain("deleteSyncStatus(");
        expect(directoryPanelSource).toContain("sources.oauth.dingtalk-directory.delete");
    });

    it("requires confirmation before deleting cached directory data", () => {
        expect(directoryPanelSource).toContain("<ak-forms-confirm");
        expect(directoryPanelSource).toContain("sources.oauth.dingtalk-directory.delete.header");
        expect(directoryPanelSource).toContain("sources.oauth.dingtalk-directory.delete.body");
    });

    it("sends the delete corp ID as a query parameter instead of a DELETE body", () => {
        expect(directoryPanelSource).toContain("query: { corp_id: corpId }");
        expect(directoryPanelSource).not.toContain(
            'method: "DELETE",\n            headers: { "Content-Type": "application/json" }',
        );
    });

    it("renders an error state instead of loading forever when the status request fails", () => {
        expect(directoryPanelSource).toContain("loadError");
        expect(directoryPanelSource).toContain("sources.oauth.dingtalk-directory.error.load");
    });

    it("renders the DingTalk directory sync schedule so its interval can be edited", () => {
        expect(directoryPanelSource).toContain("<ak-schedule-list");
        expect(directoryPanelSource).toContain(
            '.actorName=${"authentik.sources.oauth.tasks.dingtalk_directory_sync_all"}',
        );
    });

    it("keeps summary cards from overlapping long localized labels", () => {
        expect(directoryPanelSource).toContain(
            "grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr))",
        );
        expect(directoryPanelSource).toContain("display: grid;");
        expect(directoryPanelSource).toContain("row-gap: var(--pf-global--spacer--xs);");
        expect(directoryPanelSource).toContain("line-height: 1.35;");
    });

    it("covers DingTalk directory message ids in zh-Hans", () => {
        const zhHans = readFileSync(new URL("../../xliff/zh-Hans.xlf", import.meta.url), "utf8");
        const requiredIds = [
            "sources.oauth.dingtalk-directory.title",
            "sources.oauth.dingtalk-directory.summary.total",
            "sources.oauth.dingtalk-directory.summary.success",
            "sources.oauth.dingtalk-directory.summary.error",
            "sources.oauth.dingtalk-directory.summary.running",
            "sources.oauth.dingtalk-directory.summary.unknown",
            "sources.oauth.dingtalk-directory.summary.total.label",
            "sources.oauth.dingtalk-directory.summary.success.label",
            "sources.oauth.dingtalk-directory.summary.error.label",
            "sources.oauth.dingtalk-directory.summary.running.label",
            "sources.oauth.dingtalk-directory.summary.unknown.label",
            "sources.oauth.dingtalk-directory.status.success",
            "sources.oauth.dingtalk-directory.status.error",
            "sources.oauth.dingtalk-directory.status.running",
            "sources.oauth.dingtalk-directory.status.unknown",
            "sources.oauth.dingtalk-directory.refresh",
            "sources.oauth.dingtalk-directory.corp-id",
            "sources.oauth.dingtalk-directory.sync-now",
            "sources.oauth.dingtalk-directory.sync.corp-id-required",
            "sources.oauth.dingtalk-directory.sync.queued",
            "sources.oauth.dingtalk-directory.empty.title",
            "sources.oauth.dingtalk-directory.empty.body",
            "sources.oauth.dingtalk-directory.table.corp-id",
            "sources.oauth.dingtalk-directory.table.status",
            "sources.oauth.dingtalk-directory.table.started",
            "sources.oauth.dingtalk-directory.table.finished",
            "sources.oauth.dingtalk-directory.table.counters",
            "sources.oauth.dingtalk-directory.table.error",
            "sources.oauth.dingtalk-directory.table.actions",
            "sources.oauth.dingtalk-directory.timestamp.empty",
            "sources.oauth.dingtalk-directory.counters.empty",
            "sources.oauth.dingtalk-directory.error.empty",
            "sources.oauth.dingtalk-directory.docs",
            "sources.oauth.dingtalk-directory.delete",
            "sources.oauth.dingtalk-directory.delete.success",
            "sources.oauth.dingtalk-directory.delete.error",
            "sources.oauth.dingtalk-directory.delete.header",
            "sources.oauth.dingtalk-directory.delete.body",
            "sources.oauth.dingtalk-directory.error.load",
            "sources.oauth.dingtalk-directory.error.retry",
            "sources.oauth.dingtalk-directory.schedules.title",
        ];

        for (const id of requiredIds) {
            expect(zhHans).toContain(`<trans-unit id="${id}">`);
        }
    });
});
