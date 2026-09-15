import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
    localizeDingTalkDirectoryCounterKey,
    localizeDingTalkDirectoryCounterValue,
} from "#admin/sources/oauth/DingTalkDirectoryCounters";
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

const directoryPanel = readFileSync(
    resolve(import.meta.dirname, "../../src/admin/sources/oauth/DingTalkDirectoryPanel.ts"),
    "utf8",
);

const enSource = readFileSync(resolve(import.meta.dirname, "../../xliff/en.xlf"), "utf8");
const zhHans = readFileSync(resolve(import.meta.dirname, "../../xliff/zh-Hans.xlf"), "utf8");

function escapeMessageID(id: string): string {
    return id.replaceAll(".", String.raw`\.`);
}

/** The English `<source>` an XLIFF catalogue carries for a message id. */
function xliffSource(catalogue: string, id: string): string | null {
    const match = new RegExp(
        String.raw`<trans-unit id="${escapeMessageID(id)}">\s*<source>([^<]*)</source>`,
        "u",
    ).exec(catalogue);

    return match?.[1] ?? null;
}

/** The translated `<target>` an XLIFF catalogue carries for a message id. */
function xliffTarget(catalogue: string, id: string): string | null {
    const match = new RegExp(
        String.raw`<trans-unit id="${escapeMessageID(id)}">\s*<source>[^<]*</source>\s*<target>([^<]*)</target>`,
        "u",
    ).exec(catalogue);

    return match?.[1] ?? null;
}

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

    it.each([
        "dingtalk_directory_app_token_failed",
        "dingtalk_directory_corp_mismatch",
        "dingtalk_directory_corp_unauthorized",
    ])(
        // These name a specific thing the operator has to go fix, so collapsing them into
        // the generic code would put the admin back to guessing.
        "keeps the actionable %s code instead of collapsing it",
        (errorCode) => {
            expect(
                dingtalkDirectorySyncErrorCode(makeSyncStatus("corp-a", "error", { errorCode })),
            ).toBe(errorCode);
        },
    );
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

describe("localizeDingTalkDirectoryCounterKey", () => {
    it.each([
        ["departments", "Departments"],
        ["users", "Users"],
        ["warnings", "Warnings"],
        ["mode", "Sync mode"],
        ["requests", "DingTalk API requests"],
        ["user_detail_requests", "User detail requests"],
    ])("labels the %s counter reported by a sync run", (key, label) => {
        expect(localizeDingTalkDirectoryCounterKey(key)).toBe(label);
    });

    it("falls back to the raw key for a counter the UI does not know", () => {
        expect(localizeDingTalkDirectoryCounterKey("skipped_users")).toBe("skipped_users");
    });
});

describe("localizeDingTalkDirectoryCounterValue", () => {
    it("renders the sync mode as copy rather than the backend token", () => {
        expect(localizeDingTalkDirectoryCounterValue("mode", "full")).toBe("Full");
        expect(localizeDingTalkDirectoryCounterValue("mode", "incremental")).toBe("Incremental");
    });

    it.each([["" as unknown], [null], [undefined]])(
        "shows the empty message while a queued run has no mode yet (%p)",
        (value) => {
            expect(localizeDingTalkDirectoryCounterValue("mode", value)).toBe("-");
        },
    );

    it("defers to the generic rendering for an unrecognized mode", () => {
        expect(localizeDingTalkDirectoryCounterValue("mode", "delta")).toBeNull();
    });

    it("defers to the generic rendering for every counter that is not the mode", () => {
        expect(localizeDingTalkDirectoryCounterValue("requests", 42)).toBeNull();
        expect(localizeDingTalkDirectoryCounterValue("user_detail_requests", 7)).toBeNull();
        expect(localizeDingTalkDirectoryCounterValue("warnings", ["missing manager"])).toBeNull();
    });
});

describe("DingTalk directory counter catalogues", () => {
    const counterMessages = [
        ["sources.oauth.dingtalk-directory.counters.mode", "Sync mode", "同步方式"],
        [
            "sources.oauth.dingtalk-directory.counters.requests",
            "DingTalk API requests",
            "钉钉接口调用",
        ],
        [
            "sources.oauth.dingtalk-directory.counters.user-detail-requests",
            "User detail requests",
            "人员详情调用",
        ],
        ["sources.oauth.dingtalk-directory.counters.mode.full", "Full", "全量"],
        ["sources.oauth.dingtalk-directory.counters.mode.incremental", "Incremental", "增量"],
    ];

    it.each(counterMessages)("carries the English source for %s", (id, source) => {
        expect(xliffSource(enSource, id)).toBe(source);
    });

    it.each(counterMessages)(
        "carries the Simplified Chinese target for %s",
        (id, _source, target) => {
            expect(xliffTarget(zhHans, id)).toBe(target);
        },
    );
});

describe("DingTalkDirectoryPanel counter rendering", () => {
    it("localizes each counter row through the shared helpers", () => {
        expect(directoryPanel).toContain(
            "const localized = localizeDingTalkDirectoryCounterValue(key, value);",
        );
        expect(directoryPanel).toContain("${localizeDingTalkDirectoryCounterKey(key)}");
        expect(directoryPanel).toContain("${localized ?? this.renderCounterValue(value, depth)}");
    });
});
