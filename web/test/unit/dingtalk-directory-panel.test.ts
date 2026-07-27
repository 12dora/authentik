import {
    canDeleteDingTalkDirectoryStatus,
    DINGTALK_DIRECTORY_SYNC_DESTROY_CONTRACT,
    dingtalkDirectoryStatusSummary,
    dingtalkDirectorySummaryMetrics,
    dingtalkDirectorySyncErrorCode,
    DingTalkDirectorySyncStatus,
    dingtalkDirectoryTerminalEvents,
    hasRunningDingTalkDirectorySync,
    nextDingTalkDirectoryPollDelay,
} from "#admin/sources/oauth/DingTalkDirectoryPanelController";

import { describe, expect, it } from "vitest";

function makeSyncStatus(
    corpId: string,
    status: DingTalkDirectorySyncStatus["status"] | "",
    overrides: Partial<DingTalkDirectorySyncStatus> = {},
): DingTalkDirectorySyncStatus {
    return {
        corpId,
        status: status as unknown as DingTalkDirectorySyncStatus["status"],
        startedAt: null,
        finishedAt: null,
        error: "",
        errorCode: "",
        errorParams: {},
        counters: {},
        ...overrides,
    };
}

describe("dingtalkDirectoryStatusSummary", () => {
    it("counts successful, failed, running, and unknown sync statuses", () => {
        const statuses: DingTalkDirectorySyncStatus[] = [
            makeSyncStatus("corp-a", "success"),
            makeSyncStatus("corp-b", "error"),
            makeSyncStatus("corp-c", "running"),
            makeSyncStatus("corp-d", "queued"),
            makeSyncStatus("corp-e", ""),
        ];

        expect(dingtalkDirectoryStatusSummary(statuses)).toEqual({
            total: 5,
            success: 1,
            error: 1,
            running: 1,
            unknown: 2,
        });
    });
});

describe("dingtalkDirectorySummaryMetrics", () => {
    it("returns stable values with caller supplied labels", () => {
        const metrics = dingtalkDirectorySummaryMetrics(
            [makeSyncStatus("corp-a", "success"), makeSyncStatus("corp-b", "error")],
            {
                total: "Corp sync records",
                success: "Successful",
                error: "Failed",
                running: "Running",
                unknown: "Unknown",
            },
        );

        expect(metrics).toEqual([
            { key: "total", value: 2, label: "Corp sync records" },
            { key: "success", value: 1, label: "Successful" },
            { key: "error", value: 1, label: "Failed" },
            { key: "running", value: 0, label: "Running" },
        ]);
    });

    it("includes the unknown metric only when unknown statuses exist", () => {
        const metrics = dingtalkDirectorySummaryMetrics([makeSyncStatus("corp-a", "queued")], {
            total: "Corp sync records",
            success: "Successful",
            error: "Failed",
            running: "Running",
            unknown: "Unknown",
        });

        expect(metrics.at(-1)).toEqual({ key: "unknown", value: 1, label: "Unknown" });
    });
});

describe("hasRunningDingTalkDirectorySync", () => {
    it("returns true for running and transitional queued rows", () => {
        expect(hasRunningDingTalkDirectorySync([makeSyncStatus("corp-a", "running")])).toBe(true);
        expect(hasRunningDingTalkDirectorySync([makeSyncStatus("corp-a", "queued")])).toBe(true);
    });

    it("returns false for terminal rows", () => {
        expect(hasRunningDingTalkDirectorySync([makeSyncStatus("corp-a", "success")])).toBe(false);
        expect(hasRunningDingTalkDirectorySync([makeSyncStatus("corp-a", "error")])).toBe(false);
    });
});

describe("canDeleteDingTalkDirectoryStatus", () => {
    it("blocks deletion for queued and running sync rows", () => {
        expect(canDeleteDingTalkDirectoryStatus(makeSyncStatus("corp-a", "queued"))).toBe(false);
        expect(canDeleteDingTalkDirectoryStatus(makeSyncStatus("corp-a", "running"))).toBe(false);
    });

    it("allows deletion for terminal and unknown rows", () => {
        expect(canDeleteDingTalkDirectoryStatus(makeSyncStatus("corp-a", "success"))).toBe(true);
        expect(canDeleteDingTalkDirectoryStatus(makeSyncStatus("corp-a", "error"))).toBe(true);
        expect(canDeleteDingTalkDirectoryStatus(makeSyncStatus("corp-a", ""))).toBe(true);
    });
});

describe("nextDingTalkDirectoryPollDelay", () => {
    it("backs off exponentially and caps the delay", () => {
        const base = {
            maxAttempts: 60,
            baseDelayMs: 5_000,
            maxDelayMs: 60_000,
        };

        expect(nextDingTalkDirectoryPollDelay({ ...base, attempts: 0 })).toBe(5_000);
        expect(nextDingTalkDirectoryPollDelay({ ...base, attempts: 1 })).toBe(10_000);
        expect(nextDingTalkDirectoryPollDelay({ ...base, attempts: 4 })).toBe(60_000);
        expect(nextDingTalkDirectoryPollDelay({ ...base, attempts: 20 })).toBe(60_000);
    });

    it("returns null after the maximum number of attempts", () => {
        expect(
            nextDingTalkDirectoryPollDelay({
                attempts: 60,
                maxAttempts: 60,
                baseDelayMs: 5_000,
                maxDelayMs: 60_000,
            }),
        ).toBeNull();
    });
});

describe("dingtalkDirectoryTerminalEvents", () => {
    it("emits each terminal outcome once for a source, corp, status, and generation", () => {
        const seen = new Set<string>();
        const statuses = [
            { ...makeSyncStatus("corp-a", "success"), generation: 1 },
            { ...makeSyncStatus("corp-b", "error", { error: "provider denied" }), generation: 2 },
        ];

        expect(dingtalkDirectoryTerminalEvents("source-a", statuses, seen)).toEqual([
            {
                key: "source-a:corp-a:success:1",
                corpId: "corp-a",
                status: "success",
            },
            {
                key: "source-a:corp-b:error:2",
                corpId: "corp-b",
                status: "error",
                errorCode: "dingtalk_directory_sync_failed",
            },
        ]);
        expect(dingtalkDirectoryTerminalEvents("source-a", statuses, seen)).toEqual([]);
    });

    it("marks successful terminal rows with warnings as warning outcomes", () => {
        const events = dingtalkDirectoryTerminalEvents(
            "source-a",
            [
                {
                    ...makeSyncStatus("corp-a", "success", {
                        counters: { warnings: ["missing manager"] },
                    }),
                    generation: 3,
                },
            ],
            new Set<string>(),
        );

        expect(events[0]?.status).toBe("warning");
    });
});

describe("dingtalkDirectorySyncErrorCode", () => {
    it("returns null when the row reports no error", () => {
        expect(dingtalkDirectorySyncErrorCode(makeSyncStatus("corp-a", "success"))).toBeNull();
    });

    it("returns the stable code reported by the backend", () => {
        expect(
            dingtalkDirectorySyncErrorCode(
                makeSyncStatus("corp-a", "error", {
                    errorCode: "dingtalk_directory_invalid_response",
                }),
            ),
        ).toBe("dingtalk_directory_invalid_response");
    });

    it("falls back to the code carried in the legacy error field", () => {
        expect(
            dingtalkDirectorySyncErrorCode(
                makeSyncStatus("corp-a", "error", {
                    error: "dingtalk_directory_user_limit",
                }),
            ),
        ).toBe("dingtalk_directory_user_limit");
    });

    it("collapses free-form provider text to the generic failure code", () => {
        expect(
            dingtalkDirectorySyncErrorCode(
                makeSyncStatus("corp-a", "error", { error: "provider denied" }),
            ),
        ).toBe("dingtalk_directory_sync_failed");
    });
});

describe("DINGTALK_DIRECTORY_SYNC_DESTROY_CONTRACT", () => {
    it("documents the generated client operation needed for DELETE handoff", () => {
        expect(DINGTALK_DIRECTORY_SYNC_DESTROY_CONTRACT).toEqual({
            operationId: "sources_oauth_dingtalk_directory_sync_destroy",
            path: "/sources/oauth/dingtalk-directory/{source_slug}/sync/",
            method: "DELETE",
            corpIdQueryParameter: "corp_id",
        });
    });
});
