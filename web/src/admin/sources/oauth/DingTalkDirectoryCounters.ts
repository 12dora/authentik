import { msg } from "@lit/localize";

/**
 * The directory sync counters JSON carries known keys (departments/users/mode/requests/
 * user_detail_requests) plus a warnings list; localize what we recognize and fall back to
 * the raw key otherwise.
 */
export function localizeDingTalkDirectoryCounterKey(key: string): string {
    switch (key) {
        case "departments":
            return msg("Departments", {
                id: "sources.oauth.dingtalk-directory.counters.departments",
            });
        case "users":
            return msg("Users", {
                id: "sources.oauth.dingtalk-directory.counters.users",
            });
        case "warnings":
            return msg("Warnings", {
                id: "sources.oauth.dingtalk-directory.counters.warnings",
            });
        case "mode":
            return msg("Sync mode", {
                id: "sources.oauth.dingtalk-directory.counters.mode",
            });
        case "requests":
            return msg("DingTalk API requests", {
                id: "sources.oauth.dingtalk-directory.counters.requests",
            });
        case "user_detail_requests":
            return msg("User detail requests", {
                id: "sources.oauth.dingtalk-directory.counters.user-detail-requests",
            });
        default:
            return key;
    }
}

/**
 * A few counter keys carry an enum rather than a count, and those read as copy instead of
 * the raw backend token. Returns null when the generic value rendering applies.
 */
export function localizeDingTalkDirectoryCounterValue(key: string, value: unknown): string | null {
    if (key !== "mode") {
        return null;
    }

    switch (value) {
        case "full":
            return msg("Full", {
                id: "sources.oauth.dingtalk-directory.counters.mode.full",
            });
        case "incremental":
            return msg("Incremental", {
                id: "sources.oauth.dingtalk-directory.counters.mode.incremental",
            });
        // The mode is empty while a run is still queued.
        case "":
        case null:
        case undefined:
            return msg("-", { id: "sources.oauth.dingtalk-directory.counters.empty" });
        default:
            return null;
    }
}
