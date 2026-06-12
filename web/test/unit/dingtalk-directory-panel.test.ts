import { readFileSync } from "node:fs";

import type {
    DingTalkDirectoryStatusSummary,
    DingTalkDirectorySyncStatus,
} from "#admin/sources/oauth/DingTalkDirectoryPanel";

import { beforeAll, describe, expect, it } from "vitest";

let dingtalkDirectoryStatusSummary: (
    statuses: DingTalkDirectorySyncStatus[],
) => DingTalkDirectoryStatusSummary;
let dingtalkDirectorySummaryMetrics: (statuses: DingTalkDirectorySyncStatus[]) => {
    key: string;
    value: number;
    label: string;
}[];
let DingTalkDirectoryPanelElement: new () => HTMLElement;

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
        globalThis.CSSStyleSheet ??= class CSSStyleSheet {
            replaceSync(): void {
                return undefined;
            }
        } as unknown as typeof CSSStyleSheet;
        globalThis.HTMLElement ??= class HTMLElement {} as typeof HTMLElement;
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
                corp_id: "corp-a",
                status: "success",
                started_at: "2026-06-11T01:00:00Z",
                finished_at: "2026-06-11T01:02:00Z",
                error: "",
                counters: { users: 10 },
            },
            {
                corp_id: "corp-b",
                status: "error",
                started_at: "2026-06-11T02:00:00Z",
                finished_at: "2026-06-11T02:01:00Z",
                error: "missing permission",
                counters: {},
            },
            {
                corp_id: "corp-c",
                status: "running",
                started_at: "2026-06-11T03:00:00Z",
                finished_at: null,
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
                    corp_id: "corp-a",
                    status: "queued",
                    started_at: null,
                    finished_at: null,
                    error: "",
                    counters: {},
                },
                {
                    corp_id: "corp-b",
                    status: "",
                    started_at: null,
                    finished_at: null,
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
                corp_id: "corp-a",
                status: "success",
                started_at: null,
                finished_at: null,
                error: "",
                counters: {},
            },
            {
                corp_id: "corp-b",
                status: "error",
                started_at: null,
                finished_at: null,
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
                    corp_id: "corp-a",
                    status: "success",
                    started_at: null,
                    finished_at: null,
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

        expect(template).toContain("ak-dingtalk-directory-summary__value");
        expect(template).toContain("ak-dingtalk-directory-summary__label");
    });

    it("covers DingTalk directory message ids in zh-Hans", () => {
        const zhHans = readFileSync(new URL("../../xliff/zh-Hans.xlf", import.meta.url), "utf8");
        const requiredIds = [
            "sources.oauth.dingtalk-directory.title",
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
            "sources.oauth.dingtalk-directory.timestamp.empty",
            "sources.oauth.dingtalk-directory.counters.empty",
            "sources.oauth.dingtalk-directory.error.empty",
            "sources.oauth.dingtalk-directory.docs",
        ];

        for (const id of requiredIds) {
            expect(zhHans).toContain(`<trans-unit id="${id}">`);
        }
    });
});
