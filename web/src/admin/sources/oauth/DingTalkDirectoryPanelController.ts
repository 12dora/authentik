import { DingTalkDirectorySyncStatus } from "@goauthentik/api";

export type { DingTalkDirectorySyncStatus };

export interface DingTalkDirectoryStatusSummary {
    total: number;
    success: number;
    error: number;
    running: number;
    unknown: number;
}

export interface DingTalkDirectorySummaryMetric {
    key: keyof DingTalkDirectoryStatusSummary;
    value: number;
    label: string;
}

export interface DingTalkDirectoryPollState {
    attempts: number;
    maxAttempts: number;
    baseDelayMs: number;
    maxDelayMs: number;
}

export interface DingTalkDirectoryTerminalEvent {
    key: string;
    corpId: string;
    status: "success" | "error" | "warning";
    /** Stable backend error code; the panel turns it into a localized message. */
    errorCode?: DingTalkDirectorySyncErrorCode;
}

// Mirrors DINGTALK_SYNC_ERROR_CODES in authentik/sources/oauth/dingtalk/sync.py. The
// backend only ever reports one of these, so the UI never has to render raw text.
export const DINGTALK_DIRECTORY_SYNC_ERROR_CODES = [
    "dingtalk_directory_app_token_failed",
    "dingtalk_directory_broker_unavailable",
    "dingtalk_directory_concurrency_limit",
    "dingtalk_directory_corp_mismatch",
    "dingtalk_directory_corp_unauthorized",
    "dingtalk_directory_http_request_failed",
    "dingtalk_directory_invalid_response",
    "dingtalk_directory_payload_limit",
    "dingtalk_directory_run_stale",
    "dingtalk_directory_source_disabled",
    "dingtalk_directory_source_unavailable",
    "dingtalk_directory_unsupported_source",
    "dingtalk_directory_user_limit",
    "dingtalk_directory_user_detail_failed",
    "dingtalk_directory_sync_failed",
] as const;

export type DingTalkDirectorySyncErrorCode = (typeof DINGTALK_DIRECTORY_SYNC_ERROR_CODES)[number];

const SYNC_ERROR_CODES = new Set<string>(DINGTALK_DIRECTORY_SYNC_ERROR_CODES);

/**
 * Normalize the error a sync row reports into a known code.
 *
 * Rows written before the stable error contract can still hold free-form text; those
 * collapse to the generic failure code so the raw provider message never reaches the UI.
 */
export function dingtalkDirectorySyncErrorCode(
    status: Pick<DingTalkDirectorySyncStatus, "error" | "errorCode">,
): DingTalkDirectorySyncErrorCode | null {
    const candidate = (status.errorCode || status.error || "").trim();
    if (!candidate) {
        return null;
    }
    return SYNC_ERROR_CODES.has(candidate)
        ? (candidate as DingTalkDirectorySyncErrorCode)
        : "dingtalk_directory_sync_failed";
}

export type DingTalkDirectoryStatusWithGeneration = DingTalkDirectorySyncStatus & {
    generation?: number | string | null;
};

export const DINGTALK_DIRECTORY_SYNC_DESTROY_CONTRACT = {
    operationId: "sources_oauth_dingtalk_directory_sync_destroy",
    path: "/sources/oauth/dingtalk-directory/{source_slug}/sync/",
    method: "DELETE",
    corpIdQueryParameter: "corp_id",
} as const;

const TERMINAL_STATUSES = new Set(["success", "error"]);

export function dingtalkDirectoryStatusSummary(
    statuses: DingTalkDirectorySyncStatus[],
): DingTalkDirectoryStatusSummary {
    return statuses.reduce<DingTalkDirectoryStatusSummary>(
        (summary, status) => {
            summary.total += 1;
            switch (status.status) {
                case "success":
                    summary.success += 1;
                    break;
                case "error":
                    summary.error += 1;
                    break;
                case "running":
                    summary.running += 1;
                    break;
                default:
                    summary.unknown += 1;
                    break;
            }
            return summary;
        },
        {
            total: 0,
            success: 0,
            error: 0,
            running: 0,
            unknown: 0,
        },
    );
}

export function dingtalkDirectorySummaryMetrics(
    statuses: DingTalkDirectorySyncStatus[],
    labels: Record<keyof DingTalkDirectoryStatusSummary, string>,
): DingTalkDirectorySummaryMetric[] {
    const summary = dingtalkDirectoryStatusSummary(statuses);
    const metrics: DingTalkDirectorySummaryMetric[] = [
        { key: "total", value: summary.total, label: labels.total },
        { key: "success", value: summary.success, label: labels.success },
        { key: "error", value: summary.error, label: labels.error },
        { key: "running", value: summary.running, label: labels.running },
    ];

    if (summary.unknown > 0) {
        metrics.push({ key: "unknown", value: summary.unknown, label: labels.unknown });
    }

    return metrics;
}

export function hasRunningDingTalkDirectorySync(statuses: DingTalkDirectorySyncStatus[]): boolean {
    return statuses.some((status) => status.status === "running" || status.status === "queued");
}

export function canDeleteDingTalkDirectoryStatus(status: DingTalkDirectorySyncStatus): boolean {
    return status.status !== "running" && status.status !== "queued";
}

export function nextDingTalkDirectoryPollDelay(state: DingTalkDirectoryPollState): number | null {
    if (state.attempts >= state.maxAttempts) {
        return null;
    }
    const exponentialAttempt = Math.min(state.attempts, 4);
    return Math.min(state.maxDelayMs, state.baseDelayMs * 2 ** exponentialAttempt);
}

export function hasDingTalkDirectoryWarning(status: DingTalkDirectorySyncStatus): boolean {
    const counters = status.counters;
    if (!counters || typeof counters !== "object" || Array.isArray(counters)) {
        return false;
    }
    const warnings = (counters as Record<string, unknown>).warnings;
    if (Array.isArray(warnings)) {
        return warnings.length > 0;
    }
    if (typeof warnings === "number") {
        return warnings > 0;
    }
    return Boolean(warnings);
}

export function dingtalkDirectoryTerminalEvents(
    sourceSlug: string,
    statuses: DingTalkDirectoryStatusWithGeneration[],
    seenKeys: Set<string>,
): DingTalkDirectoryTerminalEvent[] {
    const events: DingTalkDirectoryTerminalEvent[] = [];

    for (const status of statuses) {
        if (!status.status || !TERMINAL_STATUSES.has(status.status)) {
            continue;
        }
        const generation =
            status.generation ?? status.finishedAt?.toISOString() ?? status.error ?? "";
        const key = [sourceSlug, status.corpId, status.status, String(generation)].join(":");
        if (seenKeys.has(key)) {
            continue;
        }
        seenKeys.add(key);

        if (status.status === "error") {
            events.push({
                key,
                corpId: status.corpId,
                status: "error",
                errorCode:
                    dingtalkDirectorySyncErrorCode(status) ?? "dingtalk_directory_sync_failed",
            });
            continue;
        }

        events.push({
            key,
            corpId: status.corpId,
            status: hasDingTalkDirectoryWarning(status) ? "warning" : "success",
        });
    }

    return events;
}
